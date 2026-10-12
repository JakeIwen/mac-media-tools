"""Own and report one foreground media subprocess."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Iterable


class TaskState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskStatus:
    state: TaskState
    running: bool
    stage: str | None
    detail: tuple[str, ...]
    elapsed: float
    current_file: str | None
    returncode: int | None
    cancelled: bool
    result: bool
    outputs: tuple[str, ...]
    errors: tuple[str, ...]

    @classmethod
    def idle(cls) -> "TaskStatus":
        return cls(
            state=TaskState.IDLE, running=False, stage=None, detail=(),
            elapsed=0.0, current_file=None, returncode=None, cancelled=False,
            result=False, outputs=(), errors=())

    def wire(self) -> dict[str, object]:
        data = asdict(self)
        data["state"] = self.state.value
        data["detail"] = list(self.detail)
        data["outputs"] = list(self.outputs)
        data["errors"] = list(self.errors)
        return data


class MediaTask:
    """A one-shot subprocess whose process group is safe to cancel."""

    _LOG_LIMIT = 64 * 1024
    _DETAIL_LINES = 12

    def __init__(self, command: list[str], log_path: Path,
                 results: Iterable[str | Path] | None = None,
                 current_file: str | Path | None = None,
                 cancel_grace: float = 3.0, terminate_grace: float = 1.0):
        if not command:
            raise ValueError("command must not be empty")
        if cancel_grace < 0 or terminate_grace < 0:
            raise ValueError("cancellation grace periods must be non-negative")
        self.command = [str(part) for part in command]
        self.log_path = Path(log_path)
        self.outputs = tuple(str(path) for path in (results or ()))
        self.current_file = str(current_file) if current_file is not None else None
        self.cancel_grace = cancel_grace
        self.terminate_grace = terminate_grace
        self._lock = threading.Lock()
        self._cancel_lock = threading.Lock()
        self._state = TaskState.IDLE
        self._process: subprocess.Popen[bytes] | None = None
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._returncode: int | None = None
        self._cancel_requested = False
        self._control_error: str | None = None
        self._monitor: threading.Thread | None = None

    def start(self) -> None:
        """Start the command once and return after successful process creation."""
        with self._lock:
            if self._state is not TaskState.IDLE:
                raise RuntimeError("media task has already been started")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log = self.log_path.open("wb", buffering=0)
            try:
                process = subprocess.Popen(
                    self.command, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)
            except BaseException:
                log.close()
                raise
            self._process = process
            self._started_at = time.monotonic()
            self._state = TaskState.RUNNING
            self._monitor = threading.Thread(
                target=self._wait, args=(process, log), daemon=True)
            self._monitor.start()

    def cancel(self) -> bool:
        """Cancel the owned process group, escalating only after bounded waits."""
        with self._cancel_lock:
            return self._cancel_once()

    def _cancel_once(self) -> bool:
        with self._lock:
            if self._state in {TaskState.IDLE, TaskState.CANCELLED,
                               TaskState.SUCCEEDED}:
                return False
            if self._state is TaskState.FAILED and not self._control_error:
                return False
            if (not self._cancel_requested and self._process is not None
                    and self._process.poll() is not None):
                monitor = self._monitor
            else:
                monitor = None
            if monitor is not None:
                process = None
            else:
                recovering = self._cancel_requested
                self._cancel_requested = True
                self._state = TaskState.CANCELLING
                self._control_error = None
                process = self._process
        if monitor is not None:
            monitor.join(timeout=1.0)
            return False
        if process is None:
            return False
        if recovering and process.poll() is not None:
            return self._recover_finished_leader(process.pid)
        try:
            if self._signal_group(process.pid, signal.SIGINT):
                if not self._wait_for_group(process.pid, self.cancel_grace):
                    self._signal_group(process.pid, signal.SIGTERM)
                    if not self._wait_for_group(process.pid, self.terminate_grace):
                        self._signal_group(process.pid, signal.SIGKILL)
                        if not self._wait_for_group(process.pid, self.terminate_grace):
                            return self._cancel_failure(
                                "Owned process group survived SIGKILL.")
        except OSError as error:
            if self._confirm_absence_after_error(process.pid):
                return self._complete_confirmed_cancel()
            return self._cancel_failure(
                f"Could not confirm owned process-group cleanup: {error}.")
        return self._complete_confirmed_cancel()

    def _complete_confirmed_cancel(self) -> bool:
        monitor = self._monitor
        if monitor is not None:
            monitor.join(timeout=max(1.0, self.terminate_grace))
            if monitor.is_alive():
                return self._cancel_failure(
                    "Process group exited, but its leader did not finish promptly.")
        return self._finish_cancelled()

    def _finish_cancelled(self) -> bool:
        with self._lock:
            self._finished_at = time.monotonic()
            self._control_error = None
            self._state = TaskState.CANCELLED
        return True

    def _confirm_absence_after_error(self, process_group: int) -> bool:
        """Retry only read-only group probes after the direct leader exits."""
        deadline = time.monotonic() + min(1.0, max(0.1, self.cancel_grace))
        process = self._process
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                try:
                    if not self._group_exists(process_group):
                        return True
                except OSError:
                    pass
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        return False

    def _recover_finished_leader(self, process_group: int) -> bool:
        """Retry only a group-existence check after its leader was reaped."""
        try:
            if self._group_exists(process_group):
                return self._cancel_failure(
                    "Owned process group still exists after its leader exited.")
        except OSError as error:
            return self._cancel_failure(
                f"Could not confirm owned process-group cleanup: {error}.")
        return self._complete_confirmed_cancel()

    def _cancel_failure(self, message: str) -> bool:
        monitor = self._monitor
        if monitor is not None:
            monitor.join(timeout=1.0)
        with self._lock:
            self._control_error = message
            self._state = TaskState.CANCELLING
        return False

    def snapshot(self) -> TaskStatus:
        with self._lock:
            state = self._state
            started_at = self._started_at
            finished_at = self._finished_at
            returncode = self._returncode
            cancelled = self._cancel_requested
            control_error = self._control_error
        detail = self._log_detail() if started_at is not None else ()
        stage = detail[-1] if detail else None
        now = finished_at if finished_at is not None else time.monotonic()
        elapsed = max(0.0, now - started_at) if started_at is not None else 0.0
        errors = ()
        if control_error:
            errors = (control_error,)
        elif state is TaskState.FAILED:
            errors = (stage or f"Command failed with exit status {returncode}.",)
        return TaskStatus(
            state=state, running=state in {TaskState.RUNNING, TaskState.CANCELLING},
            stage=stage, detail=detail, elapsed=elapsed,
            current_file=self.current_file, returncode=returncode,
            cancelled=cancelled, result=state is TaskState.SUCCEEDED,
            outputs=self.outputs, errors=errors)

    def status(self) -> dict[str, object]:
        return self.snapshot().wire()

    def _wait(self, process: subprocess.Popen[bytes], log) -> None:
        returncode = process.wait()
        log.close()
        with self._lock:
            self._returncode = returncode
            if self._cancel_requested:
                return
            self._finished_at = time.monotonic()
            if returncode == 0:
                self._state = TaskState.SUCCEEDED
            else:
                self._state = TaskState.FAILED

    def _log_detail(self) -> tuple[str, ...]:
        try:
            with self.log_path.open("rb") as log:
                log.seek(0, os.SEEK_END)
                size = log.tell()
                log.seek(max(0, size - self._LOG_LIMIT))
                text = log.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return ()
        lines = [part.strip() for line in text.splitlines()
                 for part in line.split("\r") if part.strip()]
        return tuple(lines[-self._DETAIL_LINES:])

    @staticmethod
    def _signal_group(process_group: int, sig: signal.Signals) -> bool:
        try:
            os.killpg(process_group, sig)
        except ProcessLookupError:
            return False
        return True

    @staticmethod
    def _group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        return True

    def _wait_for_group(self, process_group: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self._group_exists(process_group):
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        return True

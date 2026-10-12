import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from media_task_control import MediaTask, TaskState, TaskStatus


class MediaTaskTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def task(self, source, **kwargs):
        return MediaTask([sys.executable, "-c", source], self.root / "job.log", **kwargs)

    def wait(self, task, timeout=5):
        deadline = time.monotonic() + timeout
        while task.status()["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(task.status()["running"])
        return task.status()

    def test_success_reports_output_and_stage(self):
        task = self.task("print('Encoding source.mov', flush=True)",
                         results=[self.root / "output.mov"], current_file="source.mov")
        self.assertEqual("idle", task.status()["state"])
        task.start()
        status = self.wait(task)
        self.assertEqual("succeeded", status["state"])
        self.assertTrue(status["result"])
        self.assertEqual([str(self.root / "output.mov")], status["outputs"])
        self.assertEqual("source.mov", status["current_file"])
        self.assertEqual("Encoding source.mov", status["stage"])
        self.assertEqual(0, status["returncode"])
        self.assertGreater(status["elapsed"], 0)
        self.assertEqual(TaskState.SUCCEEDED, task.snapshot().state)
        json.dumps(status)

    def test_idle_factory_matches_fresh_default_task(self):
        task = self.task("print('unused')")
        self.assertEqual(TaskStatus.idle(), task.snapshot())
        self.assertEqual(TaskStatus.idle().wire(), task.status())

    def test_failure_reports_last_log_line(self):
        task = self.task("print('media probe failed', flush=True); raise SystemExit(7)")
        task.start()
        status = self.wait(task)
        self.assertEqual("failed", status["state"])
        self.assertEqual(7, status["returncode"])
        self.assertFalse(status["result"])
        self.assertEqual(["media probe failed"], status["errors"])

    def test_duplicate_start_is_rejected(self):
        task = self.task("print('done')")
        task.start()
        self.wait(task)
        with self.assertRaisesRegex(RuntimeError, "already been started"):
            task.start()

    def test_cancel_interrupts_owned_process_group(self):
        child_pid = self.root / "child.pid"
        child_source = "import time; time.sleep(60)"
        source = (
            "import pathlib, subprocess, sys, time\n"
            f"p=subprocess.Popen([sys.executable, '-c', {child_source!r}])\n"
            f"pathlib.Path({str(child_pid)!r}).write_text(str(p.pid))\n"
            "print('Processing source.mov', flush=True)\n"
            "try:\n time.sleep(60)\n"
            "finally:\n print('cleanup complete', flush=True)\n")
        task = self.task(source, cancel_grace=1, terminate_grace=1)
        task.start()
        deadline = time.monotonic() + 3
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(child_pid.exists())
        observed = []
        polling = threading.Thread(
            target=lambda: observed.extend(task.status()["state"] for _ in range(100)))
        polling.start()
        self.assertTrue(task.cancel())
        polling.join()
        status = self.wait(task)
        self.assertEqual("cancelled", status["state"])
        self.assertTrue(status["cancelled"])
        self.assertFalse(status["result"])
        self.assertIn("cleanup complete", status["detail"])
        self.assertTrue(set(observed) <= {"running", "cancelling", "cancelled"})
        self.assert_process_gone(int(child_pid.read_text()))
        self.assertFalse(task.cancel())

    def test_cancelling_remains_running_until_ignored_child_is_gone(self):
        child_pid = self.root / "stubborn-child.pid"
        ready = self.root / "stubborn-child.ready"
        child_source = (
            "import pathlib, signal, time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
            "time.sleep(60)\n")
        source = (
            "import signal, subprocess, sys, time\n"
            f"p=subprocess.Popen([sys.executable, '-c', {child_source!r}])\n"
            f"open({str(child_pid)!r}, 'w').write(str(p.pid))\n"
            "signal.signal(signal.SIGINT, lambda *_: sys.exit(130))\n"
            "time.sleep(60)\n")
        task = self.task(source, cancel_grace=0.4, terminate_grace=0.4)
        task.start()
        self.wait_for_path(ready)
        cancel_results = []
        cancellation = threading.Thread(target=lambda: cancel_results.append(task.cancel()))
        cancellation.start()
        time.sleep(0.1)
        status = task.status()
        self.assertEqual("cancelling", status["state"])
        self.assertTrue(status["running"])
        self.assertTrue(cancellation.is_alive())
        duplicate = threading.Thread(target=lambda: cancel_results.append(task.cancel()))
        duplicate.start()
        cancellation.join(timeout=3)
        duplicate.join(timeout=3)
        self.assertFalse(cancellation.is_alive())
        self.assertFalse(duplicate.is_alive())
        self.assertEqual([False, True], sorted(cancel_results))
        self.assertEqual("cancelled", task.status()["state"])
        self.assert_process_gone(int(child_pid.read_text()))

    def test_transient_probe_permission_error_finishes_in_one_cancel(self):
        task = self.task("import time; time.sleep(60)", cancel_grace=0.1)
        task.start()
        probes = [PermissionError(1, "Operation not permitted"), False]

        def probe(process_group):
            result = probes.pop(0) if probes else False
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(task, "_group_exists", side_effect=probe):
            self.assertTrue(task.cancel())
        self.assertEqual("cancelled", task.status()["state"])

    def test_persistent_probe_permission_error_remains_retryable(self):
        task = self.task("import time; time.sleep(60)", cancel_grace=0.1)
        task.start()
        denied = PermissionError(1, "Operation not permitted")
        with mock.patch.object(task, "_group_exists", side_effect=denied):
            self.assertFalse(task.cancel())
            status = task.status()
            self.assertEqual("cancelling", status["state"])
            self.assertTrue(status["running"])
            self.assertIn("Operation not permitted", status["errors"][0])
        with mock.patch.object(task, "_group_exists", return_value=False):
            self.assertTrue(task.cancel())
        self.assertEqual("cancelled", task.status()["state"])

    def wait_for_path(self, path):
        deadline = time.monotonic() + 3
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists())

    def assert_process_gone(self, pid):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail(f"child process {pid} survived cancellation")


if __name__ == "__main__":
    unittest.main()

"""Atomic JSON storage for custom-conversion presets and job manifests."""

import json
import os
from pathlib import Path
import secrets
import tempfile

import performance_audio as media


def write_json(path, value, exclusive=False, *, dir_fd=None):
    """Publish JSON atomically; with dir_fd, path must be a basename within it."""
    def emit(output):
        json.dump(value, output, indent=2, allow_nan=False)
        output.write("\n")
    _write_atomic(path, emit, exclusive, dir_fd=dir_fd)


def write_text(path, value, exclusive=False):
    _write_atomic(path, lambda output: output.write(value), exclusive)


def _write_atomic(path, emit, exclusive, *, dir_fd=None):
    if dir_fd is None:
        path = Path(path)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temp = Path(temporary)
    else:
        path = os.fspath(path)
        if (not isinstance(path, str) or path in ('', '.', '..')
                or any(c in path for c in ('/', '\\', '\x00'))):
            raise media.Error('Descriptor-relative publication requires a basename.')
        temp = f'.{path}.{secrets.token_hex(12)}.tmp'
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            emit(output)
            output.flush()
            os.fsync(output.fileno())
        if dir_fd is not None:
            if exclusive:
                try:
                    os.link(temp, path, src_dir_fd=dir_fd, dst_dir_fd=dir_fd,
                            follow_symlinks=False)
                except FileExistsError as error:
                    raise media.Error(f'Refusing to overwrite: {path}') from error
            else:
                os.replace(temp, path, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        elif exclusive:
            media.move_no_replace(temp, path)
        else:
            os.replace(temp, path)
    finally:
        if dir_fd is None:
            temp.unlink(missing_ok=True)
        else:
            try:
                os.unlink(temp, dir_fd=dir_fd)
            except FileNotFoundError:
                pass


def read_presets(storage, parse_preset):
    """Return valid immutable presets while isolating malformed preset files."""
    presets = {}
    warnings = []
    for path in sorted(Path(storage).glob("preset-*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            presets[path.stem] = parse_preset(value).wire()
        except (json.JSONDecodeError, UnicodeDecodeError, media.Error) as error:
            warnings.append(f"{path.name}: invalid saved preset ({error})")
    return presets, warnings

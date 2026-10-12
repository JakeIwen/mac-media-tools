"""Atomic JSON storage for custom-conversion presets and job manifests."""

import json
import os
from pathlib import Path
import tempfile

import performance_audio as media


def write_json(path, value, exclusive=False):
    """Publish private JSON atomically, optionally refusing an existing path."""
    def emit(output):
        json.dump(value, output, indent=2, allow_nan=False)
        output.write("\n")
    _write_atomic(path, emit, exclusive)


def write_text(path, value, exclusive=False):
    _write_atomic(path, lambda output: output.write(value), exclusive)


def _write_atomic(path, emit, exclusive):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            emit(output)
            output.flush()
            os.fsync(output.fileno())
        if exclusive:
            media.move_no_replace(temp, path)
        else:
            os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


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

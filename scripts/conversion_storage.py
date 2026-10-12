"""Atomic JSON storage for custom-conversion presets and job manifests."""

import json
import os
from pathlib import Path
import tempfile

import performance_audio as media
from conversion_settings import ConversionSettings


def write_json(path, value, exclusive=False):
    """Publish private JSON atomically, optionally refusing an existing path."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if exclusive:
            media.move_no_replace(temp, path)
        else:
            os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_presets(storage):
    """Return valid immutable presets while isolating malformed preset files."""
    presets = {}
    warnings = []
    for path in sorted(Path(storage).glob("preset-*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            presets[path.stem] = ConversionSettings.parse(value).wire()
        except (json.JSONDecodeError, UnicodeDecodeError, media.Error) as error:
            warnings.append(f"{path.name}: invalid saved preset ({error})")
    return presets, warnings

"""Validated interval takes and their decoded, browser-playable derivatives."""
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, asdict
from enum import Enum
from functools import cached_property
import math
import mimetypes
from pathlib import Path
import re
import sys
import tempfile
from typing import TYPE_CHECKING
import wave

sys.dont_write_bytecode = True
import bpm_over_time as bpm
import performance_audio as media

if TYPE_CHECKING:
    import numpy as np
    from interval_core import PitchTrack


class RecordingKind(str, Enum):
    REFERENCE = 'reference'
    COMPARISON = 'comparison'


RECORDING_LIMIT_BYTES = 64 * 1024 * 1024
RECORDING_LIMIT_SECONDS = 600
MIME_EXTENSIONS = {
    'audio/webm': 'webm', 'video/webm': 'webm', 'audio/ogg': 'ogg',
    'audio/mp4': 'm4a', 'video/mp4': 'mp4', 'audio/wav': 'wav',
    'audio/x-wav': 'wav', 'audio/wave': 'wav', 'audio/mpeg': 'mp3',
    'audio/aac': 'aac', 'audio/flac': 'flac', 'video/quicktime': 'mov',
}


def finite_number(value, name, minimum, maximum):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not minimum <= value <= maximum or not math.isfinite(value)):
        raise media.Error(f'{name} must be a finite number from {minimum} to {maximum}.')
    return float(value)


def media_type(value):
    if not isinstance(value, str):
        raise media.Error('An audio or video Content-Type is required.')
    result = value.split(';', 1)[0].strip().lower()
    if not re.fullmatch(r'(audio|video)/[a-z0-9!#$&^_.+\-]+', result):
        raise media.Error('An audio or video Content-Type is required.')
    return result


def recording_extension(mime):
    return MIME_EXTENSIONS.get(media_type(mime), 'bin')


def file_mime(path):
    guessed = mimetypes.guess_type(str(path))[0]
    return media_type(guessed) if guessed and guessed.startswith(('audio/', 'video/')) else 'audio/octet-stream'


@dataclass(frozen=True)
class Recording:
    kind: RecordingKind
    file: str
    mime: str
    duration: float
    size_bytes: int
    playback_delay: float
    audio_track: int

    def wire(self):
        value = asdict(self)
        value.update(kind=self.kind.value, duration=round(self.duration, 4),
                     playback_delay=round(self.playback_delay, 4))
        return value

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise media.Error('Invalid recording record.')
        try:
            kind = RecordingKind(value['kind'])
        except (TypeError, ValueError) as error:
            raise media.Error('Unknown recording kind.') from error
        name = value['file']
        if (not isinstance(name, str) or name in ('', '.', '..')
                or any(c in name for c in ('/', '\\', '\x00'))):
            raise media.Error('Recording files must be basenames.')
        mime = media_type(value['mime'])
        duration = finite_number(value['duration'], 'Duration', 0, RECORDING_LIMIT_SECONDS)
        size, track = value['size_bytes'], value['audio_track']
        delay = finite_number(value['playback_delay'], 'Playback delay', 0, 5)
        if duration <= 0 or type(size) is not int or not 0 < size <= RECORDING_LIMIT_BYTES:
            raise media.Error('Recording duration and size must be positive and within the limits.')
        if type(track) is not int or track < 0:
            raise media.Error('Audio track must be a nonnegative integer.')
        if kind == RecordingKind.REFERENCE and delay != 0:
            raise media.Error('The reference cannot have a playback delay.')
        return cls(kind, name, mime, duration, size, delay, track)


@dataclass
class Take:
    recording: Recording
    path: Path
    samples: np.ndarray
    listening: Path

    @cached_property
    def pitch(self) -> PitchTrack:
        # Pitch belongs to this captured take; replacing its peer keeps this cache.
        import interval_core
        return interval_core.track_pitch(self.samples, sample_rate=bpm.SAMPLE_RATE)


def capture_take(source, kind, mime, audio_track=0, playback_delay=0.0, *, work):
    """Own a source stream's bytes before decoding so saving uses the same audio."""
    kind = RecordingKind(kind)
    output = tempfile.NamedTemporaryFile(prefix=f'{kind.value}-',
                                         suffix='.' + recording_extension(mime),
                                         dir=work, delete=False)
    path = Path(output.name)
    try:
        with output:
            remaining = RECORDING_LIMIT_BYTES
            while chunk := source.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    raise media.Error('Recording exceeds the recording limit.')
                output.write(chunk)
        return decode_take(path, kind, mime, audio_track, playback_delay, work=work)
    except BaseException:
        with suppress(OSError):
            path.unlink(missing_ok=True)
        raise


def listening_wav(samples, kind, work):
    np, _, _ = bpm.libraries()
    with tempfile.NamedTemporaryFile(prefix=f'{kind.value}-listen-', suffix='.wav',
                                     dir=work, delete=False) as output:
        path = Path(output.name)
        try:
            with wave.open(output, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(bpm.SAMPLE_RATE)
                wav.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return path


def decode_take(source, kind, mime, audio_track=0, playback_delay=0.0, *, work):
    source, work = Path(source), Path(work)
    # Validate metadata before probing or decoding attacker-controlled media.
    record = Recording.parse(dict(kind=kind, file=source.name, mime=mime, duration=1,
                                  size_bytes=source.stat().st_size,
                                  playback_delay=playback_delay, audio_track=audio_track))
    before = media.fingerprint(source)
    media.stream(media.probe(source), 'audio', audio_track)
    # bpm.decode uses a fixed raw filename; isolate each invocation, including retries.
    with tempfile.TemporaryDirectory(prefix='decode-', dir=work) as scratch:
        samples, _ = bpm.decode(source, Path(scratch), audio_track,
                                limit_seconds=RECORDING_LIMIT_SECONDS)
    np, _, _ = bpm.libraries()
    samples = np.asarray(samples, dtype=np.float32)
    duration = len(samples) / bpm.SAMPLE_RATE
    if (not 0 < duration <= RECORDING_LIMIT_SECONDS or samples.ndim != 1
            or not np.isfinite(samples).all()):
        raise media.Error('Audio must contain finite samples and last at most 600 seconds.')
    if media.fingerprint(source) != before:
        raise media.Error('The source changed during decoding. Choose it again.')
    record = Recording.parse({**record.wire(), 'duration': duration})
    listening = listening_wav(samples, record.kind, work)
    return Take(record, source, samples, listening)

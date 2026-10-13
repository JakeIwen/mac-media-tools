"""Version-1 interval-analysis contract, naming and validated wire conversion."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import Enum
import json
import sys
from typing import TYPE_CHECKING, TypedDict

sys.dont_write_bytecode = True
import performance_audio as media
from interval_takes import RecordingKind, finite_number

if TYPE_CHECKING:
    from interval_core import PitchTrack

# Stored version-1 limits must not change when the DSP search is retuned.
V1_ALIGNMENT_BOUND = 5.75

INTERVAL_NAMES = (
    ("Unison", "P1"), ("Minor second", "m2"), ("Major second", "M2"),
    ("Minor third", "m3"), ("Major third", "M3"), ("Perfect fourth", "P4"),
    ("Tritone", "TT"), ("Perfect fifth", "P5"), ("Minor sixth", "m6"),
    ("Major sixth", "M6"), ("Minor seventh", "m7"), ("Major seventh", "M7"),
    ("Octave", "P8"),
)


class AlignmentMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    HINT = "hint"


ALIGNMENT_MODES = tuple(mode.value for mode in AlignmentMode)


class IntervalLabel(TypedDict):
    semitones: int
    name: str
    short: str
    direction: str


class IntervalName(IntervalLabel):
    simple: int
    octaves: int


class SimpleInterval(IntervalName):
    agreement: float


class IntervalAnswer(SimpleInterval):
    cents_median: float


class HistogramBin(IntervalLabel):
    frames: int
    share: float


class Segment(IntervalLabel):
    start: float
    end: float


class TimelinePoint(TypedDict):
    time: float
    reference_hz: float | None
    comparison_hz: float | None
    cents: float | None
    smoothed_cents: float | None
    semitones: int | None


class TrackSummary(TypedDict):
    duration: float
    voiced_seconds: float


class AlignmentRecord(TypedDict):
    offset_seconds: float
    confidence: float
    mode: str
    hint_seconds: float
    warning: str | None


class AnalysisParameters(TypedDict):
    sample_rate: float
    hop: int
    frame_length: int
    fmin: float
    fmax: float
    stabilize_seconds: float
    hysteresis_cents: float
    min_label_seconds: float
    max_offset_seconds: float


def interval_name(semitones: int) -> IntervalName:
    semitones = int(semitones)
    magnitude = abs(semitones)
    octaves, remainder = divmod(magnitude, 12)
    if magnitude <= 12:
        name, short = INTERVAL_NAMES[magnitude]
    else:
        name = "Octave" if octaves == 1 else "Two octaves" if octaves == 2 else f"{octaves} octaves"
        short = "P8" if octaves == 1 else f"{octaves}P8"
        if remainder:
            component, abbreviation = INTERVAL_NAMES[remainder]
            name += " + " + component.lower()
            short += "+" + abbreviation
    return {"semitones": semitones, "simple": remainder * (-1 if semitones < 0 else 1),
            "octaves": octaves, "direction": "above" if semitones > 0 else "below" if semitones < 0 else "unison",
            "name": name, "short": short}


@dataclass(frozen=True)
class Alignment:
    offset_seconds: float
    confidence: float
    mode: AlignmentMode
    hint_seconds: float
    warning: str | None

    def wire(self) -> AlignmentRecord:
        return {"offset_seconds": round(float(self.offset_seconds), 4),
                "confidence": round(float(self.confidence), 4), "mode": AlignmentMode(self.mode).value,
                "hint_seconds": round(float(self.hint_seconds), 4), "warning": self.warning}


def interval_label(semitones: int) -> IntervalLabel:
    name = interval_name(semitones)
    return {key: name[key] for key in IntervalLabel.__annotations__}


@dataclass(frozen=True)
class AnalysisResult:
    alignment: AlignmentRecord
    overlap_seconds: float
    reference: TrackSummary
    comparison: TrackSummary
    interval: IntervalAnswer | None
    simple_interval: SimpleInterval | None
    histogram: list[HistogramBin]
    simple_histogram: list[HistogramBin]
    segments: list[Segment]
    timeline: list[TimelinePoint]
    warnings: list[str]
    method: str
    parameters: AnalysisParameters

    @classmethod
    def from_dsp(cls, *, reference: PitchTrack, comparison: PitchTrack,
                 alignment: Alignment, overlap: float, interval: IntervalAnswer | None,
                 simple_interval: SimpleInterval | None, histogram: list[HistogramBin],
                 simple_histogram: list[HistogramBin], segments: list[Segment],
                 timeline: list[TimelinePoint], warnings: list[str], stabilize_seconds: float,
                 hysteresis_cents: float, min_label_seconds: float, max_offset: float):
        def summary(track):
            return {"duration": round(float(track.duration), 4),
                    "voiced_seconds": round(track.voiced_seconds(), 4)}

        return cls(
            alignment.wire(), round(overlap, 4), summary(reference), summary(comparison),
            interval, simple_interval, histogram, simple_histogram, segments, timeline, warnings,
            "pYIN fundamental frequency; voicing-landmark alignment; running median and hysteresis labels.",
            {"sample_rate": reference.sample_rate, "hop": reference.hop,
             "frame_length": reference.frame_length, "fmin": reference.fmin, "fmax": reference.fmax,
             "stabilize_seconds": stabilize_seconds, "hysteresis_cents": hysteresis_cents,
             "min_label_seconds": min_label_seconds, "max_offset_seconds": max_offset},
        )

    def wire(self) -> dict:
        return {"version": 1, **asdict(self)}

    def interval_summary(self) -> dict | None:
        if self.interval is None:
            return None
        return {key: self.interval[key] for key in ('name', 'short', 'direction', 'semitones', 'agreement')}


def parse(value) -> AnalysisResult | None:
    value = validate_analysis(value)
    if value is None:
        return None
    return AnalysisResult(**{field.name: value[field.name] for field in fields(AnalysisResult)})


def validate_interval(value, extra=()):
    if not isinstance(value, dict) or type(value.get('semitones')) is not int:
        raise media.Error('Invalid saved interval.')
    expected = interval_name(value['semitones'])
    if any(value.get(key) != expected[key] for key in ('name', 'short', 'direction')):
        raise media.Error('Invalid saved interval name or direction.')
    for key in extra:
        if key in ('simple', 'octaves'):
            if type(value.get(key)) is not int or value[key] != expected[key]:
                raise media.Error(f'Invalid saved interval {key}.')
        elif key == 'agreement':
            finite_number(value.get(key), key, 0, 1)
        elif key == 'cents_median':
            finite_number(value.get(key), key, -1e9, 1e9)
    return value


def validate_series(analysis):
    for key in ('histogram', 'simple_histogram', 'segments', 'timeline'):
        if not isinstance(analysis.get(key), list):
            raise media.Error(f'Invalid saved {key}.')
    for key in ('histogram', 'simple_histogram'):
        for row in analysis[key]:
            validate_interval(row)
            if type(row.get('frames')) is not int or row['frames'] < 1:
                raise media.Error('Invalid histogram frame count.')
            finite_number(row.get('share'), 'Histogram share', 0, 1)
    for row in analysis['segments']:
        validate_interval(row)
        start = finite_number(row.get('start'), 'Segment start', 0, 605)
        finite_number(row.get('end'), 'Segment end', start, 605)
    if len(analysis['timeline']) > 2400:
        raise media.Error('Saved timeline is too long.')
    for row in analysis['timeline']:
        if not isinstance(row, dict):
            raise media.Error('Invalid saved timeline frame.')
        finite_number(row.get('time'), 'Timeline time', 0, 605)
        for key in ('reference_hz', 'comparison_hz', 'cents', 'smoothed_cents'):
            if key not in row:
                raise media.Error('Incomplete saved timeline frame.')
            if row[key] is not None:
                low = 0 if key.endswith('_hz') else -1e9
                finite_number(row[key], key, low, 1e9)
        if 'semitones' not in row or (row['semitones'] is not None and type(row['semitones']) is not int):
            raise media.Error('Invalid timeline interval.')


def validate_analysis(value):
    if value is None:
        return None
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        raise media.Error('Invalid saved analysis version.')
    try:
        value = json.loads(json.dumps(value, allow_nan=False))
        alignment = value['alignment']
        if not isinstance(alignment, dict) or alignment.get('mode') not in ALIGNMENT_MODES:
            raise media.Error('Invalid alignment mode.')
        bound = V1_ALIGNMENT_BOUND
        finite_number(alignment['offset_seconds'], 'Alignment offset', -bound, bound)
        finite_number(alignment['hint_seconds'], 'Alignment hint', -5, 5)
        finite_number(alignment['confidence'], 'Alignment confidence', 0, 1)
        if alignment['warning'] is not None and not isinstance(alignment['warning'], str):
            raise media.Error('Invalid alignment warning.')
        finite_number(value['overlap_seconds'], 'Voiced overlap', 0, 605)
        for kind in RecordingKind:
            recording = value[kind.value]
            duration = finite_number(recording['duration'], 'Analysis duration', 0, 600)
            finite_number(recording['voiced_seconds'], 'Voiced duration', 0, duration + 1)
        for key in ('interval', 'simple_interval'):
            if value[key] is not None:
                extra = ('simple', 'octaves', 'agreement')
                validate_interval(value[key], extra + (('cents_median',) if key == 'interval' else ()))
        if (value['interval'] is None) != (value['simple_interval'] is None):
            raise media.Error('Inconsistent saved interval answers.')
        if not isinstance(value['warnings'], list) or not all(isinstance(w, str) for w in value['warnings']):
            raise media.Error('Invalid saved warnings.')
        if not isinstance(value['method'], str) or not isinstance(value['parameters'], dict):
            raise media.Error('Invalid analysis method or parameters.')
        for key in AnalysisParameters.__annotations__:
            finite_number(value['parameters'][key], key, 0, 1e9)
        validate_series(value)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise media.Error(f'Invalid saved analysis: {error}') from error
    return value



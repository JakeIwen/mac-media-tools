"""Monophonic pitch tracking and timing-aligned singing interval analysis."""

from dataclasses import dataclass
import math
import sys

sys.dont_write_bytecode = True
import bpm_over_time as bpm
from interval_result import (Alignment, AlignmentMode, ALIGNMENT_MODES, AnalysisResult,
                             interval_label, interval_name)

np, librosa, signal = bpm.libraries()
from scipy.ndimage import median_filter

SAMPLE_RATE = bpm.SAMPLE_RATE
HOP = 256
FRAME_LENGTH = 2048
FMIN, FMAX = 65.0, 1100.0
MAX_OFFSET_SECONDS = 0.75
ALIGNMENT_PEAK_SEPARATION_SECONDS = 0.10
MIN_ALIGNMENT_CONFIDENCE = 0.25
MIN_TIMING_PEAK_MARGIN = 0.20
STABILIZE_SECONDS = 0.25
HYSTERESIS_CENTS = 60
MIN_LABEL_SECONDS = 0.10
MIN_SEGMENT_SECONDS = 0.25
SEGMENT_GAP_SECONDS = 0.30
MIN_OVERLAP_SECONDS = 0.5
MIN_VOICED_CONFIDENCE = 0.1


@dataclass(frozen=True)
class PitchTrack:
    times: np.ndarray
    f0: np.ndarray
    voiced: np.ndarray
    confidence: np.ndarray
    hop_seconds: float
    duration: float
    sample_rate: float = SAMPLE_RATE
    hop: int = HOP
    frame_length: int = FRAME_LENGTH
    fmin: float = FMIN
    fmax: float = FMAX

    def tracking_parameters(self):
        return self.sample_rate, self.hop, self.frame_length, self.fmin, self.fmax

    def voiced_seconds(self) -> float:
        return float(np.count_nonzero(self.voiced) * self.hop_seconds)


def track_pitch(samples, sample_rate=SAMPLE_RATE, fmin=FMIN, fmax=FMAX, hop=HOP) -> PitchTrack:
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim != 1 or not samples.size or not np.isfinite(samples).all():
        raise bpm.media.Error("Audio must contain finite mono samples and have positive duration.")
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise bpm.media.Error("Sample rate must be finite and positive.")
    if not (math.isfinite(fmin) and math.isfinite(fmax) and 0 < fmin < fmax <= sample_rate / 2):
        raise bpm.media.Error("Pitch bounds must be positive and below the Nyquist frequency.")
    if isinstance(hop, bool) or not isinstance(hop, int) or hop <= 0:
        raise bpm.media.Error("Pitch hop must be a positive integer.")
    f0, voiced, confidence = librosa.pyin(
        samples, fmin=fmin, fmax=fmax, sr=sample_rate,
        frame_length=FRAME_LENGTH, hop_length=hop, fill_na=np.nan,
    )
    # Viterbi can extend voicing into a mostly silent onset window; exclude its
    # low-probability guesses so those windows cannot become timing landmarks.
    voiced = (np.asarray(voiced, dtype=bool) & (confidence >= MIN_VOICED_CONFIDENCE)
              & np.isfinite(f0) & (f0 > 0))
    f0 = np.where(voiced, f0, np.nan).astype(np.float64)
    times = np.arange(len(f0), dtype=np.float64) * hop / sample_rate
    return PitchTrack(times, f0, voiced, np.asarray(confidence, dtype=np.float64),
                      hop / sample_rate, len(samples) / sample_rate,
                      sample_rate, hop, FRAME_LENGTH, fmin, fmax)


def _landmarks(track, length):
    # Exterior silence retains recording-edge attacks even for sustained notes.
    mask = np.pad(np.asarray(track.voiced, dtype=np.float64), (1, length - len(track.voiced) + 1))
    boundaries = np.abs(np.diff(mask, prepend=0))
    boundaries = np.convolve(boundaries, [0.25, 0.5, 0.25], mode="same")
    return mask - np.mean(mask), boundaries


def _overlapping_slices(reference_length, comparison_length, lag):
    start = max(0, -lag)
    end = min(reference_length, comparison_length - lag)
    end = max(start, end)
    return slice(start, end), slice(start + lag, end + lag)


def _peak_separation_confidence(scores, best, hop):
    distance = max(1, math.ceil(ALIGNMENT_PEAK_SEPARATION_SECONDS / hop))
    # Include boundary maxima, but not neighboring samples from the winning peak.
    peaks, _ = signal.find_peaks(np.pad(scores, 1, constant_values=-np.inf), distance=distance)
    peaks -= 1
    competitors = peaks[np.abs(peaks - best) >= distance]
    if not len(competitors):
        return 1.0
    peak = float(scores[best])
    if peak <= 1e-9:
        return 0.0
    competitor = float(scores[competitors].max())
    # Competing timings within a 20% score margin must not look conclusive.
    margin = (peak - competitor) / peak
    return float(np.clip(MIN_ALIGNMENT_CONFIDENCE * margin / MIN_TIMING_PEAK_MARGIN, 0, 1))


def align_offset(reference, comparison, hint=0.0, max_offset=MAX_OFFSET_SECONDS) -> Alignment:
    if not math.isfinite(hint) or not math.isfinite(max_offset) or max_offset < 0:
        raise bpm.media.Error("Alignment hint and search width must be finite, with a nonnegative width.")
    hop = reference.hop_seconds
    if hop <= 0 or not math.isclose(hop, comparison.hop_seconds):
        raise bpm.media.Error("Pitch tracks must use the same positive hop duration.")
    first = math.ceil((hint - max_offset) / hop)
    last = math.floor((hint + max_offset) / hop)
    lags = np.arange(first, last + 1, dtype=int)
    if not len(lags):
        lags = np.array([round(hint / hop)], dtype=int)
    length = max(len(reference.voiced), len(comparison.voiced), 1)
    reference_signals = _landmarks(reference, length)
    comparison_signals = _landmarks(comparison, length)
    scores = np.zeros(len(lags), dtype=np.float64)
    for ref, comp in zip(reference_signals, comparison_signals):
        denominator = float(np.linalg.norm(ref) * np.linalg.norm(comp))
        if denominator > 1e-12:
            correlation = signal.correlate(comp, ref, mode="full", method="fft") / denominator
            positions = lags + len(ref) - 1
            valid = (positions >= 0) & (positions < len(correlation))
            scores[valid] += correlation[positions[valid]]
    best = min(np.flatnonzero(np.isclose(scores, scores.max(), atol=1e-10)),
               key=lambda i: abs(lags[i] * hop - hint))
    lag = int(lags[best])
    ref_slice, comp_slice = _overlapping_slices(len(reference.voiced), len(comparison.voiced), lag)
    overlap = np.count_nonzero(reference.voiced[ref_slice] & comparison.voiced[comp_slice])
    spread = float(scores[best] - scores.min())
    confidence = float(np.clip((scores[best] - np.median(scores)) / spread, 0, 1)) if spread > 1e-9 else 0.0
    peak_confidence = _peak_separation_confidence(scores, best, hop)
    confidence = min(confidence, peak_confidence)
    if overlap < 20:
        confidence = 0.0
    warning = None
    if best in (0, len(lags) - 1):
        warning = f"Alignment search hit its limit (±{max_offset:g} s)."
    if confidence < MIN_ALIGNMENT_CONFIDENCE:
        warning = "Alignment is uncertain; the measured playback delay was kept." + (" " + warning if warning else "")
        if peak_confidence < MIN_ALIGNMENT_CONFIDENCE:
            warning += " Competing timings were found."
        return Alignment(hint, 0.0, AlignmentMode.HINT, hint, warning)
    return Alignment(lag * hop, confidence, AlignmentMode.AUTO, hint, warning)


def _runs(values):
    """Yield half-open runs; adjacent missing labels stay a single missing run."""
    if not len(values):
        return
    same = (values[1:] == values[:-1]) | (np.isnan(values[1:]) & np.isnan(values[:-1]))
    boundaries = np.concatenate(([0], np.flatnonzero(~same) + 1, [len(values)]))
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        yield int(start), int(end)


def _absorb_short_labels(labels, minimum_frames):
    runs = list(_runs(labels))
    stable = [end - start >= minimum_frames for start, end in runs]
    # Only an original stable neighbour absorbs a short run (a tracking glitch
    # beside a held note). A chain of short runs is a moving passage, such as a
    # sweep or trill, and must stay varied rather than cascade into one label.
    for index, (start, end) in enumerate(runs):
        if stable[index]:
            continue
        if index and stable[index - 1]:
            labels[start:end] = labels[runs[index - 1][0]]
        elif index + 1 < len(runs) and stable[index + 1]:
            labels[start:end] = labels[runs[index + 1][0]]
    return labels


def _stabilize(cents, hop):
    smoothed = np.full(len(cents), np.nan, dtype=np.float64)
    labels = smoothed.copy()
    window = max(1, round(STABILIZE_SECONDS / hop))
    window += 1 - window % 2
    minimum_frames = math.ceil(MIN_LABEL_SECONDS / hop)
    valid = np.isfinite(cents)
    for start, end in _runs(valid):
        if not valid[start]:
            continue
        local = median_filter(cents[start:end], size=window, mode="nearest")
        smoothed[start:end] = local
        current = round(float(local[0]) / 100)
        for index, value in enumerate(local, start):
            if abs(value - 100 * current) > HYSTERESIS_CENTS:
                current = round(float(value) / 100)
            labels[index] = current
        _absorb_short_labels(labels[start:end], minimum_frames)
    return smoothed, labels


def _histogram(labels):
    values, counts = np.unique(labels[np.isfinite(labels)].astype(int), return_counts=True)
    total = int(counts.sum())
    bins = []
    for value, count in zip(values, counts):
        bins.append(interval_label(int(value))
                    | {"frames": int(count), "share": round(int(count) / total, 4)})
    return sorted(bins, key=lambda item: (-item["frames"], item["semitones"]))


def _segments(labels, times, hop, duration):
    runs = []
    for start, end in _runs(labels):
        if not np.isfinite(labels[start]):
            continue
        label = int(labels[start])
        if (end - start) * hop < MIN_SEGMENT_SECONDS:
            continue
        if (runs and runs[-1][2] == label and (start - runs[-1][1]) * hop < SEGMENT_GAP_SECONDS
                and np.isnan(labels[runs[-1][1]:start]).all()):
            runs[-1] = (runs[-1][0], end, label)
        else:
            runs.append((start, end, label))
    segments = []
    for start, end, label in runs:
        left, right = float(times[start]), min(float(times[end - 1] + hop), duration)
        if right - left < MIN_SEGMENT_SECONDS:
            continue
        segments.append({"start": round(left, 4), "end": round(right, 4)} | interval_label(label))
    return segments


def _aligned_frequencies(reference, comparison, offset):
    lag = round(offset / reference.hop_seconds)
    ref_slice, comp_slice = _overlapping_slices(len(reference.f0), len(comparison.f0), lag)
    ref = np.where(reference.voiced & np.isfinite(reference.f0) & (reference.f0 > 0), reference.f0, np.nan)
    comp = np.full(len(ref), np.nan, dtype=np.float64)
    local = comparison.f0[comp_slice]
    comp[ref_slice] = np.where(comparison.voiced[comp_slice] & np.isfinite(local) & (local > 0), local, np.nan)
    return ref, comp


def _timeline(times, reference, comparison, cents, smoothed, labels):
    points = []
    step = max(1, math.ceil(len(times) / 2400))
    for index in range(0, len(times), step):
        point = {"time": round(float(times[index]), 4)}
        for name, series, places in (("reference_hz", reference, 2), ("comparison_hz", comparison, 2),
                                     ("cents", cents, 1), ("smoothed_cents", smoothed, 1)):
            point[name] = round(float(series[index]), places) if np.isfinite(series[index]) else None
        point["semitones"] = int(labels[index]) if np.isfinite(labels[index]) else None
        points.append(point)
    return points


def _analysis_warnings(reference, comparison, overlap, interval, alignment):
    warnings = [alignment.warning] if alignment.warning else []
    if overlap < 2:
        warnings.append("Less than 2 s of overlapping singing.")
    if interval and interval["agreement"] < 0.6:
        warnings.append(f"The interval is inconsistent (agreement {interval['agreement']:.0%}). Check the timeline and segments.")
    for name, track in (("reference", reference), ("comparison", comparison)):
        if track.voiced_seconds() < 0.5:
            warnings.append(f"The {name} take has almost no voiced audio.")
    return warnings


def compare_tracks(reference, comparison, hint=0.0, manual_offset=None,
                   max_offset=MAX_OFFSET_SECONDS) -> dict:
    if reference.tracking_parameters() != comparison.tracking_parameters():
        raise bpm.media.Error("Pitch tracks must use the same tracking parameters.")
    for track in (reference, comparison):
        if (track.sample_rate <= 0 or track.hop <= 0 or track.hop_seconds <= 0
                or not math.isclose(track.hop_seconds, track.hop / track.sample_rate)):
            raise bpm.media.Error("Pitch track hop duration must match its tracking parameters.")
    if not math.isfinite(max_offset) or max_offset < 0:
        raise bpm.media.Error("Alignment search width must be finite and nonnegative.")
    if manual_offset is None:
        alignment = align_offset(reference, comparison, hint, max_offset)
    else:
        if not math.isfinite(manual_offset) or not math.isfinite(hint):
            raise bpm.media.Error("Alignment offsets must be finite.")
        alignment = Alignment(manual_offset, 1.0, AlignmentMode.MANUAL, hint, None)
    if reference.hop_seconds <= 0 or not math.isclose(reference.hop_seconds, comparison.hop_seconds):
        raise bpm.media.Error("Pitch tracks must use the same positive hop duration.")
    ref, comp = _aligned_frequencies(reference, comparison, alignment.offset_seconds)
    valid = np.isfinite(ref) & np.isfinite(comp)
    cents = np.full(len(ref), np.nan, dtype=np.float64)
    cents[valid] = 1200 * (np.log2(comp[valid]) - np.log2(ref[valid]))
    smoothed, labels = _stabilize(cents, reference.hop_seconds)
    histogram = _histogram(labels)
    folded = np.sign(labels) * (np.abs(labels) % 12)
    simple_histogram = _histogram(folded)
    overlap = float(np.count_nonzero(valid) * reference.hop_seconds)
    interval, simple_interval = None, None
    if overlap >= MIN_OVERLAP_SECONDS:
        winner = histogram[0]
        mode = winner["semitones"]
        interval = interval_name(mode) | {"agreement": winner["share"],
            "cents_median": round(float(np.median(smoothed[labels == mode]) - 100 * mode), 1)}
        simple_interval = interval_name(simple_histogram[0]["semitones"]) | {"agreement": simple_histogram[0]["share"]}
    result = AnalysisResult.from_dsp(
        reference=reference, comparison=comparison, alignment=alignment, overlap=overlap,
        interval=interval, simple_interval=simple_interval, histogram=histogram,
        simple_histogram=simple_histogram,
        segments=_segments(labels, reference.times, reference.hop_seconds, reference.duration),
        timeline=_timeline(reference.times, ref, comp, cents, smoothed, labels),
        warnings=_analysis_warnings(reference, comparison, overlap, interval, alignment),
        stabilize_seconds=STABILIZE_SECONDS, hysteresis_cents=HYSTERESIS_CENTS,
        min_label_seconds=MIN_LABEL_SECONDS, max_offset=max_offset,
    )
    return result.wire()


def analyze_pair(reference_samples, comparison_samples, sample_rate=SAMPLE_RATE,
                 hint=0.0, manual_offset=None) -> dict:
    reference = track_pitch(reference_samples, sample_rate=sample_rate)
    comparison = track_pitch(comparison_samples, sample_rate=sample_rate)
    return compare_tracks(reference, comparison, hint=hint, manual_offset=manual_offset)

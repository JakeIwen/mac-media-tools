"""Synthetic singing and direct pitch-track regressions for interval analysis."""

from dataclasses import replace
import json
import unittest

import numpy as np
import interval_core as core
import interval_result

RATE = core.SAMPLE_RATE


def tone(frequency, duration=3.2, vibrato_rate=0.0, vibrato_cents=0.0, leading=0.0):
    times = np.arange(round(duration * RATE), dtype=np.float64) / RATE
    frequency_curve = frequency * np.exp2(vibrato_cents * np.sin(2 * np.pi * vibrato_rate * times) / 1200)
    phase = 2 * np.pi * np.cumsum(frequency_curve) / RATE
    samples = sum(np.sin(harmonic * phase) / harmonic ** 1.5 for harmonic in range(1, 5)) * 0.22
    samples += np.random.default_rng(81).normal(0, 0.0002, len(samples))
    fade = min(round(0.025 * RATE), len(samples) // 2)
    samples[:fade] *= np.linspace(0, 1, fade)
    samples[-fade:] *= np.linspace(1, 0, fade)
    return np.concatenate((np.zeros(round(leading * RATE)), samples)).astype(np.float32)


def notes(semitones, durations, gap=0.10, base=220.0):
    pieces = []
    for index, (semitone, duration) in enumerate(zip(semitones, durations)):
        if index:
            pieces.append(np.zeros(round(gap * RATE), dtype=np.float32))
        pieces.append(tone(base * 2 ** (semitone / 12), duration))
    return np.concatenate(pieces)


def pitch_track(frequencies, hop=core.HOP / RATE):
    f0 = np.asarray(frequencies, dtype=np.float64)
    voiced = np.isfinite(f0) & (f0 > 0)
    return core.PitchTrack(np.arange(len(f0), dtype=np.float64) * hop, f0, voiced,
                           voiced.astype(np.float64), hop, len(f0) * hop,
                           sample_rate=RATE, hop=round(hop * RATE))


class IntervalCoreTests(unittest.TestCase):
    def analyze(self, reference, comparison, **kwargs):
        self.assertLessEqual(len(reference) / RATE, 6)
        self.assertLessEqual(len(comparison) / RATE, 6)
        result = core.analyze_pair(reference, comparison, **kwargs)
        json.dumps(result, allow_nan=False)
        self.assertLessEqual(len(result["timeline"]), 2400)
        return result

    def compare(self, reference, comparison, **kwargs):
        result = core.compare_tracks(reference, comparison, **kwargs)
        json.dumps(result, allow_nan=False)
        return result

    def test_result_contract_round_trips_wire_without_changing_order_or_rounding(self):
        reference = pitch_track(np.full(120, 220.0))
        for frequencies in (np.full(120, 330.0), np.full(120, np.nan)):
            with self.subTest(voiced=np.isfinite(frequencies).any()):
                wire = self.compare(reference, pitch_track(frequencies), manual_offset=0.123456)
                result = interval_result.parse(wire)
                self.assertIsInstance(result, interval_result.AnalysisResult)
                self.assertEqual(json.dumps(result.wire()), json.dumps(wire))
                self.assertEqual(result.wire()['alignment']['offset_seconds'], 0.1235)
                wire['parameters']['hop'] = 1
                self.assertEqual(result.wire()['parameters']['hop'], core.HOP)
                exported = result.wire()
                exported['timeline'].clear()
                self.assertTrue(result.wire()['timeline'])
        self.assertIsNone(interval_result.parse(None))

    def test_nondefault_tracking_parameters_describe_the_actual_pitch_grid(self):
        options = dict(sample_rate=24000, hop=480, fmin=80.0, fmax=900.0)
        reference = core.track_pitch(tone(220, duration=0.8), **options)
        comparison = core.track_pitch(tone(330, duration=0.8), **options)
        wire = self.compare(reference, comparison, manual_offset=0)
        self.assertEqual({key: wire['parameters'][key] for key in options}, options)
        self.assertEqual(wire['parameters']['frame_length'], reference.frame_length)
        self.assertEqual(reference.frame_length, core.FRAME_LENGTH)
        self.assertAlmostEqual(reference.hop_seconds, options['hop'] / options['sample_rate'])
        self.assertEqual(wire['timeline'][1]['time'], .02)
        self.assertEqual(interval_result.parse(wire).wire(), wire)

    def test_compare_rejects_mismatched_tracking_parameters_and_invented_grid(self):
        reference = pitch_track(np.full(120, 220.0))
        mismatches = dict(sample_rate=24000, hop=512, frame_length=4096, fmin=80, fmax=900)
        for key, value in mismatches.items():
            for manual in (None, 0):
                with self.subTest(key=key, manual=manual), self.assertRaises(core.bpm.media.Error):
                    self.compare(reference, replace(reference, **{key: value}), manual_offset=manual)
        invented = replace(reference, hop_seconds=.02)
        with self.assertRaisesRegex(core.bpm.media.Error, 'tracking parameters'):
            self.compare(invented, invented, manual_offset=0)

    def test_result_parser_rejects_invalid_scalar_records(self):
        track = pitch_track(np.full(120, 220.0))
        original = self.compare(track, track, manual_offset=0)
        invalid = [
            (('version',), True), (('version',), 2), (('alignment',), []),
            (('alignment', 'mode'), 'guess'), (('alignment', 'offset_seconds'), 5.76),
            (('alignment', 'hint_seconds'), -5.01), (('alignment', 'confidence'), 1.1),
            (('alignment', 'warning'), 123), (('overlap_seconds',), -1),
            (('reference', 'duration'), 601), (('comparison', 'voiced_seconds'), 605),
            (('interval', 'semitones'), True), (('interval', 'name'), 'wrong'),
            (('interval', 'short'), 'wrong'), (('interval', 'direction'), 'above'),
            (('interval', 'simple'), True), (('interval', 'octaves'), 1),
            (('interval', 'agreement'), -1), (('interval', 'cents_median'), 1e10),
            (('simple_interval',), None), (('warnings',), ['valid', 1]),
            (('method',), False), (('parameters',), []), (('parameters', 'hop'), True),
            (('parameters', 'fmin'), float('nan')),
        ]
        for keys, value in invalid:
            with self.subTest(keys=keys), self.assertRaises(core.bpm.media.Error):
                wire = json.loads(json.dumps(original))
                parent = wire
                for key in keys[:-1]:
                    parent = parent[key]
                parent[keys[-1]] = value
                interval_result.parse(wire)
        for key in original:
            with self.subTest(missing=key), self.assertRaises(core.bpm.media.Error):
                interval_result.parse({k: v for k, v in original.items() if k != key})

    def test_result_parser_rejects_invalid_series_records(self):
        track = pitch_track(np.full(120, 220.0))
        original = self.compare(track, track, manual_offset=0)
        invalid = [
            ('histogram', 'frames', True), ('simple_histogram', 'frames', 0),
            ('histogram', 'share', 1.1), ('segments', 'start', -1),
            ('segments', 'end', 606), ('timeline', 'time', -1),
            ('timeline', 'reference_hz', -1), ('timeline', 'comparison_hz', float('inf')),
            ('timeline', 'cents', True), ('timeline', 'smoothed_cents', 'bad'),
            ('timeline', 'semitones', 1.5),
        ]
        for series, key, value in invalid:
            with self.subTest(series=series, key=key), self.assertRaises(core.bpm.media.Error):
                wire = json.loads(json.dumps(original))
                wire[series][0][key] = value
                interval_result.parse(wire)
        for series in ('histogram', 'simple_histogram', 'segments', 'timeline'):
            for value in ({}, [None]):
                with self.subTest(series=series, value=value), self.assertRaises(core.bpm.media.Error):
                    interval_result.parse({**original, series: value})
        with self.assertRaises(core.bpm.media.Error):
            interval_result.parse({**original, 'timeline': original['timeline'] * 21})
        for key in original['timeline'][0]:
            with self.subTest(missing=key), self.assertRaises(core.bpm.media.Error):
                point = {k: v for k, v in original['timeline'][0].items() if k != key}
                interval_result.parse({**original, 'timeline': [point]})

    def test_fifth_with_delay_and_independent_vibrato(self):
        result = self.analyze(tone(220, vibrato_rate=5.5, vibrato_cents=40),
                              tone(329.63, vibrato_rate=4.8, vibrato_cents=60, leading=0.15))
        self.assertEqual(result["interval"]["semitones"], 7)
        self.assertGreaterEqual(result["interval"]["agreement"], 0.9)
        self.assertEqual(result["alignment"]["mode"], core.AlignmentMode.AUTO.value)
        self.assertAlmostEqual(result["alignment"]["offset_seconds"], 0.15, delta=0.03)
        self.assertEqual(len(result["segments"]), 1)
        segment = result["segments"][0]
        self.assertGreaterEqual(segment["end"] - segment["start"], 0.9 * result["overlap_seconds"])

    def test_synchronized_diatonic_thirds_do_not_shift_to_musical_answer(self):
        reference = notes([0, 2, 4, 5, 7, 9, 11, 12], [0.3] * 8)
        comparison = notes([4, 5, 7, 9, 11, 12, 14, 16], [0.3] * 8)
        result = self.analyze(reference, comparison)
        self.assertAlmostEqual(result["alignment"]["offset_seconds"], 0, delta=core.HOP / RATE)
        self.assertTrue({entry["semitones"] for entry in result["histogram"][:2]} <= {3, 4})

    def test_drifting_repeated_notes_retain_hint_when_timings_compete(self):
        sequence = np.arange(6) * 2
        reference = notes(sequence, [0.50] * 6)
        for duration in (0.54, 0.46):
            comparison = notes(sequence + 4, [duration] * 6)
            for delay in (0.0, 0.15):
                with self.subTest(comparison_note_seconds=duration, hint=delay):
                    delayed = np.pad(comparison, (round(delay * RATE), 0))
                    result = self.analyze(reference, delayed, hint=delay)
                    alignment = result["alignment"]
                    self.assertEqual(alignment["mode"], core.AlignmentMode.HINT.value)
                    self.assertEqual(alignment["confidence"], 0)
                    self.assertEqual(alignment["offset_seconds"], delay)
                    self.assertEqual(result["interval"]["semitones"], 4)
                    self.assertIn("Alignment is uncertain", alignment["warning"])
                    self.assertIn("Competing timings", alignment["warning"])
                    self.assertIn(alignment["warning"], result["warnings"])

    def test_major_third_below(self):
        result = self.analyze(tone(220), tone(220 * 2 ** (-4 / 12)))
        self.assertEqual(result["interval"]["semitones"], -4)
        self.assertEqual(result["interval"]["direction"], "below")
        self.assertEqual(result["interval"]["name"], "Major third")

    def test_compound_major_third(self):
        result = self.analyze(tone(220), tone(220 * 2 ** (16 / 12)))
        self.assertEqual(result["interval"]["semitones"], 16)
        self.assertEqual(result["simple_interval"]["semitones"], 4)

    def test_simple_histogram_folds_every_label_not_only_the_mode(self):
        durations = [1.75, 1.70, 1.55]
        result = self.analyze(notes([0, 0, 0], durations), notes([7, 4, 16], durations))
        self.assertEqual(result["interval"]["semitones"], 7)
        self.assertEqual(result["simple_interval"]["semitones"], 4)
        self.assertGreaterEqual(result["simple_interval"]["agreement"], 0.58)
        self.assertLessEqual(result["simple_interval"]["agreement"], 0.72)
        folded_thirds = sum(entry["frames"] for entry in result["histogram"] if entry["semitones"] in (4, 16))
        self.assertEqual(result["simple_histogram"][0]["frames"], folded_thirds)

    def test_third_switching_to_fifth(self):
        result = self.analyze(notes([0, 0], [1.6, 1.6]), notes([4, 7], [1.6, 1.6]))
        self.assertEqual([segment["semitones"] for segment in result["segments"]], [4, 7])
        self.assertTrue(all(segment["end"] - segment["start"] >= 1 for segment in result["segments"]))

    def test_unison_and_octaves(self):
        result = self.analyze(tone(220), tone(220))
        self.assertEqual(result["interval"]["semitones"], 0)
        self.assertEqual(result["interval"]["direction"], "unison")
        reference = pitch_track(np.full(120, 220.0))
        comparison = pitch_track(np.full(120, 440.0))
        octave = self.compare(reference, comparison, manual_offset=0)
        self.assertEqual(octave["simple_interval"]["semitones"], 0)
        self.assertEqual(octave["simple_histogram"][0]["share"], 1)

    def test_silence_returns_no_answer(self):
        result = self.analyze(tone(220), np.zeros(3 * RATE, dtype=np.float32))
        self.assertIsNone(result["interval"])
        self.assertIsNone(result["simple_interval"])
        self.assertEqual(result["histogram"], [])
        self.assertIn("The comparison take has almost no voiced audio.", result["warnings"])
        self.assertTrue(all(point["cents"] is None for point in result["timeline"]))
        self.assertEqual(result["alignment"]["confidence"], 0)

    def test_manual_offset_overrides_search_and_outside_takes_are_empty(self):
        reference = pitch_track(np.full(200, 220.0))
        comparison = pitch_track(np.full(220, 330.0))
        manual = self.compare(reference, comparison, hint=0.3, manual_offset=-0.12)
        self.assertEqual(manual["alignment"], {"offset_seconds": -0.12, "hint_seconds": 0.3,
                                              "confidence": 1.0, "mode": "manual", "warning": None})
        for offset in (-5, 5):
            result = self.compare(reference, comparison, manual_offset=offset)
            self.assertIsNone(result["interval"])
            self.assertEqual(result["overlap_seconds"], 0)
            self.assertTrue(result["warnings"])
            self.assertTrue(all(point["comparison_hz"] is None for point in result["timeline"]))

    def test_interval_names(self):
        expected = {0: ("Unison", "P1", 0, 0, "unison"),
                    7: ("Perfect fifth", "P5", 7, 0, "above"),
                    12: ("Octave", "P8", 0, 1, "above"),
                    16: ("Octave + major third", "P8+M3", 4, 1, "above"),
                    24: ("Two octaves", "2P8", 0, 2, "above"),
                    28: ("Two octaves + major third", "2P8+M3", 4, 2, "above"),
                    -7: ("Perfect fifth", "P5", -7, 0, "below")}
        for semitones, wanted in expected.items():
            with self.subTest(semitones=semitones):
                name = core.interval_name(semitones)
                self.assertEqual(name["semitones"], semitones)
                self.assertEqual(tuple(name[key] for key in ("name", "short", "simple", "octaves", "direction")), wanted)

    def test_tracker_validates_samples_and_returns_float64_tracks(self):
        for invalid in ([], [np.nan], [np.inf], [[0.1, 0.2]]):
            with self.subTest(invalid=invalid), self.assertRaises(core.bpm.media.Error):
                core.track_pitch(invalid)
        track = core.track_pitch(tone(220, duration=0.8))
        self.assertEqual(track.f0.dtype, np.float64)
        self.assertEqual(track.times.dtype, np.float64)
        self.assertEqual(track.confidence.dtype, np.float64)
        self.assertEqual(track.voiced.dtype, np.bool_)
        self.assertAlmostEqual(float(np.nanmedian(track.f0)), 220, delta=2)
        self.assertAlmostEqual(track.voiced_seconds(), np.count_nonzero(track.voiced) * track.hop_seconds)

    def test_alignment_uses_only_voicing_landmarks_and_handles_negative_lag(self):
        shape = np.concatenate((np.zeros(25), np.ones(35), np.zeros(20), np.ones(90), np.zeros(10)))
        reference = pitch_track(np.where(shape, 220.0, np.nan))
        late = pitch_track(np.where(np.pad(shape, (13, 0)), 550.0, np.nan))
        expected = 13 * reference.hop_seconds
        alignment = core.align_offset(reference, late)
        self.assertEqual(alignment.mode, core.AlignmentMode.AUTO)
        self.assertAlmostEqual(alignment.offset_seconds, expected)
        changing = pitch_track(np.where(late.voiced, np.linspace(80, 1000, len(late.f0)), np.nan))
        self.assertEqual(alignment, core.align_offset(reference, changing))
        self.assertAlmostEqual(core.align_offset(late, reference).offset_seconds, -expected)
        edge = core.align_offset(reference, late, max_offset=expected)
        self.assertIn("search hit its limit", edge.warning)

    def test_uncertain_alignment_retains_hint_and_short_overlap_has_no_answer(self):
        silent = pitch_track(np.full(100, np.nan))
        reference = pitch_track(np.full(100, 220.0))
        alignment = core.align_offset(reference, silent, hint=0.123)
        self.assertEqual(alignment.mode, core.AlignmentMode.HINT)
        self.assertEqual(alignment.offset_seconds, 0.123)
        self.assertEqual(alignment.confidence, 0)
        result = self.compare(reference, pitch_track(np.full(20, 330.0)), manual_offset=0)
        self.assertIsNone(result["interval"])
        self.assertTrue(result["histogram"])

    def test_stabilization_does_not_bridge_missing_frames_but_segments_can(self):
        reference = pitch_track(np.full(300, 220.0))
        frequencies = np.full(300, 220 * 2 ** (4 / 12))
        frequencies[80:90] = np.nan
        frequencies[180:220] = np.nan
        result = self.compare(reference, pitch_track(frequencies), manual_offset=0)
        for index in [*range(80, 90), *range(180, 220)]:
            point = result["timeline"][index]
            self.assertIsNone(point["cents"])
            self.assertIsNone(point["smoothed_cents"])
            self.assertIsNone(point["semitones"])
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(result["histogram"][0]["frames"], 250)
        self.assertAlmostEqual(result["overlap_seconds"], 250 * reference.hop_seconds, delta=0.0001)

    def test_short_labels_are_absorbed_at_start_and_after_real_changes(self):
        reference = pitch_track(np.full(220, 220.0))
        labels = np.array([3] * 3 + [4] * 105 + [7] * 3 + [4] * 109)
        comparison = pitch_track(220 * np.exp2(labels / 12))
        result = self.compare(reference, comparison, manual_offset=0)
        self.assertEqual([entry["semitones"] for entry in result["histogram"]], [4])
        self.assertEqual(len(result["segments"]), 1)

    def test_octave_sweep_without_stable_labels_remains_inconsistent(self):
        duration = 0.8
        times = np.arange(round(duration * RATE)) / RATE
        comparison = tone(220 * np.exp2(times / duration), duration=duration)
        result = self.analyze(tone(220, duration=duration), comparison, manual_offset=0)
        self.assertLess(result["interval"]["agreement"], 0.5)
        self.assertEqual(result["segments"], [])
        self.assertTrue(any("interval is inconsistent" in warning for warning in result["warnings"]))

    def test_short_label_chain_is_not_absorbed_by_a_distant_stable_run(self):
        # A monotonic passage survives the running median, exposing label absorption.
        labels = np.repeat(np.arange(13), 8)
        for stable_index in (0, 6, 12):
            with self.subTest(stable_label=stable_index):
                original = np.insert(labels, stable_index * 8, [stable_index] * 20)
                reference = pitch_track(np.full(len(original), 220.0))
                comparison = pitch_track(220 * np.exp2(original / 12))
                result = self.compare(reference, comparison, manual_offset=0)
                self.assertEqual(result["interval"]["semitones"], stable_index)
                # Only the short runs beside the held note join it; the rest stay varied.
                self.assertLess(result["interval"]["agreement"], 0.4)
                self.assertGreater(len(result["histogram"]), 8)
                self.assertTrue(any("inconsistent" in warning for warning in result["warnings"]))

    def test_short_labels_join_only_an_adjacent_original_stable_run(self):
        labels = np.repeat(np.arange(13), [8] * 3 + [28] + [8] * 5 + [28] + [8] * 3)
        reference = pitch_track(np.full(len(labels), 220.0))
        comparison = pitch_track(220 * np.exp2(labels / 12))
        result = self.compare(reference, comparison, manual_offset=0)
        expected = ([0] * 8 + [1] * 8 + [3] * 44 + [5] * 8 + [6] * 8 + [7] * 8
                    + [9] * 44 + [11] * 8 + [12] * 8)
        self.assertEqual([point["semitones"] for point in result["timeline"]], expected)

    def test_short_voiced_islands_do_not_become_a_segment_through_gap_merging(self):
        reference = pitch_track(np.full(120, 220.0))
        frequencies = np.full(120, np.nan)
        frequencies[10:20] = 330.0
        frequencies[30:40] = 330.0
        result = self.compare(reference, pitch_track(frequencies), manual_offset=0)
        self.assertIsNone(result["interval"])
        self.assertEqual(result["segments"], [])

    def test_invalid_alignment_values_are_rejected_even_in_manual_mode(self):
        reference = pitch_track(np.full(100, 220.0))
        for options in ({"hint": float("nan")}, {"max_offset": -1},
                        {"manual_offset": float("inf")}, {"manual_offset": 0, "max_offset": float("nan")}):
            with self.subTest(options=options), self.assertRaises(core.bpm.media.Error):
                core.compare_tracks(reference, reference, **options)

    def test_long_timeline_is_bounded_and_folded_intervals_keep_direction(self):
        reference = pitch_track(np.full(6001, 220.0))
        labels = np.array([-16] * 3100 + [-4] * 1800 + [7] * 1101)
        result = self.compare(reference, pitch_track(220 * np.exp2(labels / 12)), manual_offset=0)
        self.assertLessEqual(len(result["timeline"]), 2400)
        self.assertEqual(result["simple_interval"]["semitones"], -4)
        self.assertEqual(result["simple_interval"]["direction"], "below")
        self.assertGreater(result["simple_interval"]["agreement"], 0.8)


if __name__ == "__main__":
    unittest.main()

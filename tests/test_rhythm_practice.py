import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rhythm_practice as rhythm
import numpy as np

class RhythmTests(unittest.TestCase):
    def test_attack_times_and_silent_audio(self):
        rate=rhythm.bpm.SAMPLE_RATE
        samples=np.zeros(12*rate,dtype=np.float32)
        times=np.arange(.5,11.6,.5)
        rng=np.random.default_rng(123)
        pulse=(rng.normal(0,.4,int(.02*rate))*np.exp(-np.arange(int(.02*rate))/(rate*.004))).astype(np.float32)
        for t in times:samples[int(t*rate):int(t*rate)+len(pulse)]+=pulse
        r=rhythm.analyze(samples,120)
        self.assertEqual(len(r['events']),len(times))
        actual=np.array([e['time'] for e in r['events']])
        self.assertLess(float(np.max(np.abs(actual-times))),.03)
        self.assertLessEqual(len(r['waveform']),3600)
        self.assertEqual(rhythm.analyze(np.zeros(8*rate,dtype=np.float32))['events'],[])

    def test_report_playback_collision_and_escaping(self):
        rhythm.bpm.configure_tools()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'Jake & {{DATA}}.wav'
            with wave.open(str(source),'wb') as f:
                f.setparams((1,2,rhythm.bpm.SAMPLE_RATE,0,'NONE','not compressed'))
                f.writeframes(np.zeros(4*rhythm.bpm.SAMPLE_RATE,dtype='<i2').tobytes())
            before=source.read_bytes()
            page=rhythm.analyze_file(source,reference_bpm=90)
            self.assertTrue((page.parent/'playback.m4a').exists())
            self.assertIn('Jake &amp; {{DATA}}.wav',page.read_text())
            data=json.loads((page.parent/'analysis.json').read_text())
            self.assertEqual(data['reference']['bpm'],90)
            data['source']='bad<script>alert(1)</script>.wav'
            self.assertNotIn('<script>alert(1)</script>',rhythm.render(data))
            self.assertNotEqual(page,rhythm.analyze_file(source,reference_bpm=90))
            self.assertEqual(source.read_bytes(),before)

if __name__=='__main__':unittest.main()

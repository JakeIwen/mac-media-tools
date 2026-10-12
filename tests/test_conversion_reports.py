"""Central report identities and authenticated report-view behavior."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import conversion_reports as reports
import performance_audio as media
import performance_audio_effects as effects
from conversion_editor import EditorServer
from conversion_jobs import ConversionJobs, ConversionProgress
from conversion_storage import write_json


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, {'PERFORMANCE_AUDIO_REPORTS_DIR': str(self.root/'reports')})
        environment.start()
        self.addCleanup(environment.stop)

    def output(self):
        path = self.root/'finished.mp4'
        path.write_bytes(b'first output')
        return path

    def test_generations_remain_distinct_after_filename_reuse_or_deletion(self):
        output = self.output()
        first = reports.generation_id(output)
        reports.save_audio(output, {'take': 'first'}, 'First report')
        output.unlink()
        output.write_bytes(b'second output, different content')
        second = reports.generation_id(output)
        reports.save_audio(output, {'take': 'second'}, 'Second report')
        self.assertNotEqual(first, second)
        output.unlink()
        text = reports.read_report(first, reports.artifacts(first)[0])['content']
        self.assertEqual(json.loads(text)['take'], 'first')
        self.assertEqual(len(reports.artifacts(second)), 2)
        self.assertFalse(list(self.root.glob('*.audio-report.*')))

    def test_missing_reports_after_publication_are_valid_and_legacy_files_untouched(self):
        output = self.output()
        legacy = output.with_name(output.name+'.audio-report.txt')
        legacy.write_text('Existing report')
        generation = reports.generation_id(output)
        self.assertEqual(reports.artifacts(generation), [])
        reports.save_audio(output, {'take': 1}, 'New report')
        self.assertEqual(legacy.read_text(), 'Existing report')

    def test_reader_rejects_path_escape_symlink_and_oversized_file(self):
        output = self.output()
        generation = reports.generation_id(output)
        paths = reports.save_audio(output, {'take': 1}, 'New report')
        with self.assertRaises(media.Error): reports.directory('../foreign')
        with self.assertRaises(media.Error): reports.read_report(generation, reports.ReportArtifact('../foreign', 'bad'))
        with patch.object(reports, 'REPORT_LIMIT', 1):
            with self.assertRaises(media.Error): reports.read_report(generation, reports.artifacts(generation)[0])
        paths[0].unlink()
        paths[0].symlink_to(output)
        with self.assertRaises(media.Error): reports.read_report(generation, reports.ReportArtifact(paths[0].name, 'bad'))

    def test_report_endpoint_is_authenticated_and_bound_to_job_output(self):
        output = self.output()
        generation = reports.generation_id(output)
        reports.save_audio(output, {'take': 1}, 'New report')
        jobs = ConversionJobs([], effects.DEFAULT_CONFIG, self.root/'jobs')
        jobs.job = self.root/'jobs/job-owned'; jobs.job.mkdir()
        write_json(jobs.job/'progress.json', asdict(ConversionProgress(outputs=[str(output)], report_ids=[generation])))
        with EditorServer(jobs) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            self.addCleanup(server.shutdown)
            artifact = reports.artifacts(generation)[0]
            payload = {'job_id': jobs.job.name, 'output_index': 0, 'report_index': 0, 'name': artifact.name}
            def request(value, headers):
                with urlopen(Request(server.origin+'/report', data=json.dumps(value).encode(), headers=headers)) as response:
                    return json.load(response)
            with self.assertRaises(HTTPError) as caught: request(payload, {})
            self.assertEqual(caught.exception.code, 403)
            headers = {'X-Editor-Token': server.token, 'Content-Type': 'application/json'}
            output.unlink()
            self.assertEqual(json.loads(request(payload, headers)['content'])['take'], 1)
            for invalid in ({'output_index': True}, {'report_index': -1}, {'job_id': 'another-job'}, {'name': '../foreign'}):
                with self.assertRaises(HTTPError): request({**payload, **invalid}, headers)
            server.shutdown()


if __name__ == '__main__': unittest.main()

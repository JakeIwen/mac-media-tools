"""Conversion recipes, supervised jobs and output-authoritative results."""
from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path
import sys
import threading
import uuid

import convert_video
import performance_audio as media
import performance_audio_effects as effects
from conversion_settings import ConversionSettings, VideoSettings
from conversion_storage import read_presets, write_json
from media_task_control import MediaTask, TaskStatus


@dataclass
class ConversionProgress:
    current_file: str = ''
    index: int = 0
    total: int = 0
    outputs: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @classmethod
    def read(cls, path):
        return cls(**json.loads(path.read_text()))


@dataclass
class ConversionResult:
    path: Path
    warnings: list = field(default_factory=list)


class ConversionJobs:
    def __init__(self, files, config, storage, output_dir=None, audio_track=0, slug='custom'):
        self.files = [Path(f).resolve() for f in files]
        self.config = Path(config)
        self.storage = Path(storage)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.output_dir = output_dir
        self.audio_track = audio_track
        self.slug = slug
        self.lock = threading.RLock()
        self.task = None
        self.job = None

    def presets(self):
        data = effects.load_presets(self.config)
        result = {name: ConversionSettings(preset, VideoSettings()).wire()
                  for name, preset in data['presets'].items()}
        saved, warnings = read_presets(self.storage)
        result.update(saved)
        return result, warnings

    def save(self, settings):
        path = self.storage / f'preset-{uuid.uuid4().hex}.json'
        write_json(path, settings.wire(), exclusive=True)
        return path.stem

    def status(self):
        with self.lock:
            task = self.task.snapshot() if self.task else TaskStatus.idle()
            progress = ConversionProgress(total=len(self.files))
            if self.job and (self.job/'progress.json').exists():
                progress = ConversionProgress.read(self.job/'progress.json')
            task = replace(task, outputs=tuple(progress.outputs), current_file=progress.current_file,
                           errors=task.errors + tuple(progress.errors))
            return {'task': task.wire(), 'progress': asdict(progress),
                    'job_id': self.job.name if self.job else None}

    def is_running(self):
        return self.task is not None and self.task.snapshot().running

    def cancel(self):
        with self.lock:
            task = self.task
        return task.cancel() if task else False

    def start(self, settings):
        with self.lock:
            if self.is_running(): raise media.Error('A conversion is already running.')
            if not self.files: raise media.Error('Choose at least one video first.')
            job = self.storage / f'job-{uuid.uuid4().hex}'
            job.mkdir()
            request = {'files': [str(p) for p in self.files], 'settings': settings.wire(),
                       'audio_track': self.audio_track, 'slug': self.slug,
                       'output_dir': str(self.output_dir) if self.output_dir else None}
            write_json(job/'request.json', request, exclusive=True)
            write_json(job/'progress.json', asdict(ConversionProgress(total=len(self.files))))
            task = MediaTask([sys.executable, str(Path(__file__).with_name('conversion_worker.py')), str(job)], job/'processing.log')
            task.start()
            self.job, self.task = job, task

    def convert_one(self, source, settings, job, config, on_published=None):
        parent = self.output_dir or source.parent
        info = media.probe(source)
        before = media.fingerprint(source)
        staging = job / uuid.uuid4().hex
        staging.mkdir()
        intermediate = source
        if settings.audio_enabled:
            intermediate = effects.process(source, self.slug, config, share=settings.share,
                audio_track=self.audio_track, output_dir=parent if settings.video.mode == 'none' else staging,
                on_published=on_published if settings.video.mode == 'none' else None)
        if settings.video.mode != 'none':
            result = convert_video.convert(intermediate, settings.video.resolution, parent,
                audio_track=0 if settings.audio_enabled else self.audio_track, video_options=settings.video, on_published=on_published)
        elif settings.audio_enabled:
            result = intermediate
        else:
            result = self.copy_original(source, parent, settings, info, before, on_published)
        outcome = ConversionResult(result)
        if media.fingerprint(source) != before:
            outcome.warnings.append('Source changed after processing; review the saved output.')
        self.save_recipe(source, settings, intermediate, outcome)
        return outcome

    def copy_original(self, source, parent, settings, info, before, on_published):
        destination = parent / (source.stem + '-custom' + media.replacement_suffix(info, settings.share))
        video, audio = media.stream(info, 'video'), media.stream(info, 'audio', self.audio_track)
        def validate(output):
            media.validate_video(info, output, 1)
            if media.fingerprint(source) != before: raise media.Error('Source changed during processing.')
        return media.publish(['-i', source, '-map', f"0:{video['index']}", '-map', f"0:{audio['index']}",
                              '-c', 'copy', *media.mov_options()], destination, validate,
                             numbered=True, preserve_completed=True, on_published=on_published)

    @staticmethod
    def save_recipe(source, settings, intermediate, outcome):
        recipe = outcome.path.with_name(outcome.path.name+'.conversion.json')
        audio_report = intermediate.with_name(intermediate.name+'.audio-report.json')
        try:
            details = json.loads(audio_report.read_text()) if settings.audio_enabled and audio_report.exists() else None
            write_json(recipe, {'source': str(source), 'output': str(outcome.path),
                                'settings': settings.wire(), 'audio_report': details}, exclusive=True)
        except (OSError, ValueError) as error:
            outcome.warnings.append(f'Video saved, but recipe could not be saved: {error}')

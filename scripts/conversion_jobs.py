"""One-shot conversions and immutable user presets for the local editor."""
import json
from pathlib import Path
import threading
import uuid

import convert_video
import performance_audio as media
import performance_audio_effects as effects
from conversion_settings import ConversionSettings, VideoSettings


class ConversionJobs:
    def __init__(self, files, config, storage, output_dir=None):
        self.files = [Path(f).resolve() for f in files]
        self.config = Path(config)
        self.storage = Path(storage)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.output_dir = output_dir
        self.lock = threading.Lock()
        self.state = {'running': False, 'outputs': [], 'errors': []}

    def presets(self):
        data = effects.load_presets(self.config)
        result = {name: ConversionSettings(preset, VideoSettings()).wire()
                  for name, preset in data['presets'].items()}
        for path in sorted(self.storage.glob('preset-*.json')):
            result[path.stem] = ConversionSettings.parse(json.loads(path.read_text())).wire()
        return result

    def save(self, settings):
        path = self.storage / f'preset-{uuid.uuid4().hex}.json'
        with path.open('x', encoding='utf-8') as file:
            json.dump(settings.wire(), file, indent=2, allow_nan=False)
        return path.stem

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def start(self, settings):
        with self.lock:
            if self.state['running']: raise media.Error('A conversion is already running.')
            self.state = {'running': True, 'outputs': [], 'errors': []}
        thread = threading.Thread(target=self._run, args=(settings,), daemon=False)
        thread.start()

    def _run(self, settings):
        job = self.storage / f'job-{uuid.uuid4().hex}'
        try:
            job.mkdir()
            config = job / 'audio.json'
            config.write_text(json.dumps({'default': 'custom', 'presets': {'custom': settings.audio}}))
            for source in self.files:
                try:
                    output = self.convert_one(source, settings, job, config)
                    with self.lock: self.state['outputs'].append(str(output))
                except Exception as error:
                    with self.lock: self.state['errors'].append(f'{source.name}: {error}')
        except Exception as error:
            with self.lock: self.state['errors'].append(str(error))
        finally:
            with self.lock: self.state['running'] = False
            if job.is_dir():
                (job / 'result.json').write_text(json.dumps(self.status(), indent=2))

    def convert_one(self, source, settings, job, config):
        parent = self.output_dir or source.parent
        info = media.probe(source)
        original_fingerprint = media.fingerprint(source)
        staging = job / uuid.uuid4().hex
        staging.mkdir()
        intermediate = source
        if settings.audio_enabled:
            destination = parent if settings.video.mode == 'none' else staging
            intermediate = effects.process(source, 'custom', config, share=settings.share,
                                           output_dir=destination)
        if settings.video.mode != 'none':
            result = convert_video.convert(intermediate, settings.video.resolution, parent,
                                            video_options=settings.video)
        elif settings.audio_enabled:
            result = intermediate
        else:
            extension = media.replacement_suffix(info, settings.share)
            destination = parent / (source.stem + '-custom' + extension)
            args = ['-i', source, '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy', *media.mov_options()]
            result = media.publish(args, destination, lambda out: media.validate_video(info, out, 1),
                                   numbered=True, preserve_completed=True)
        if media.fingerprint(source) != original_fingerprint:
            raise media.Error(f'Source changed during processing. Review output: {result}')
        # The recipe records the exact one-off controls, including the video choice.
        recipe = result.with_name(result.name + '.conversion.json')
        audio_report = intermediate.with_name(intermediate.name + '.audio-report.json')
        details = json.loads(audio_report.read_text()) if settings.audio_enabled and audio_report.exists() else None
        with recipe.open('x') as file:
            json.dump({'source': str(source), 'output': str(result), 'settings': settings.wire(),
                       'audio_report': details}, file, indent=2)
        return result

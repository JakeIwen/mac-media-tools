#!/usr/bin/env python3
"""Foreground worker owned and cancelled only by its editor session."""
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import convert_video
import performance_audio as media
from conversion_jobs import ConversionJobs, ConversionProgress
from conversion_settings import ConversionSettings
from conversion_storage import write_json


def run_job(folder):
    request = json.loads((folder/'request.json').read_text())
    settings = ConversionSettings.parse(request['settings'])
    slug = request['slug']
    config = folder/'audio.json'
    write_json(config, {'default': slug, 'presets': {slug: settings.audio}}, exclusive=True)
    output = Path(request['output_dir']) if request['output_dir'] else None
    jobs = ConversionJobs(request['files'], config, folder, output, request['audio_track'], slug)
    progress = ConversionProgress(total=len(jobs.files))
    def record_output(path):
        progress.outputs.append(str(path))
        write_json(folder/'progress.json', asdict(progress))
    try:
        for index, source in enumerate(jobs.files, 1):
            progress.index, progress.current_file = index, str(source)
            write_json(folder/'progress.json', asdict(progress))
            print(f'Processing {index} of {progress.total}: {source.name}', flush=True)
            try:
                result = jobs.convert_one(source, settings, folder, config, on_published=record_output)
                progress.warnings.extend(result.warnings)
            except (media.Error, OSError, ValueError) as error:
                progress.errors.append(f'{source.name}: {error}')
            write_json(folder/'progress.json', asdict(progress))
    except KeyboardInterrupt:
        print('Cancelled. Previously completed outputs are retained.', flush=True)
        return 130
    finally:
        write_json(folder/'progress.json', asdict(progress))
    return 1 if progress.errors else 0


if __name__ == '__main__':
    convert_video.configure_tools()
    sys.exit(run_job(Path(sys.argv[1]).resolve()))

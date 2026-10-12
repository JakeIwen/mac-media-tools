"""Central report store keyed by an output's captured generation identity."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re

import performance_audio as media
from conversion_storage import write_json, write_text

REPORT_LIMIT = 2 * 1024 * 1024
REPORT_NAME = re.compile(r'(?:audio-report(?:-\d+)?\.(?:txt|json)|conversion\.json)\Z')


@dataclass(frozen=True)
class ReportArtifact:
    name: str
    label: str

    def wire(self):
        return asdict(self)


def report_root():
    override = os.environ.get('PERFORMANCE_AUDIO_REPORTS_DIR')
    pointer = Path(__file__).with_name('reports-path.txt')
    root = Path(override) if override else Path(pointer.read_text().strip()) if pointer.exists() else Path(__file__).resolve().parents[1]/'build/conversion-reports'
    return root.expanduser().resolve()


def generation_id(output):
    payload = {'output': str(output.resolve()), **media.fingerprint(output)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def directory(generation, create=False):
    if not isinstance(generation, str) or not re.fullmatch(r'[a-f0-9]{64}', generation):
        raise media.Error('Invalid report generation.')
    root = report_root()
    path = root/generation
    if path.resolve().parent != root: raise media.Error('Report directory escaped its store.')
    if create: path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def output_directory(output):
    return directory(generation_id(output), create=True)


def save_audio(output, report, text):
    folder = output_directory(output)
    number = 1
    while True:
        suffix = '' if number == 1 else f'-{number}'
        data = folder/f'audio-report{suffix}.json'
        try:
            write_json(data, report, exclusive=True)
            break
        except FileExistsError:
            number += 1
    readable = folder/f'audio-report{suffix}.txt'
    write_text(readable, text, exclusive=True)
    return [readable, data]


def artifacts(generation):
    folder = directory(generation)
    if not folder.exists(): return []
    result = []
    for path in sorted(folder.iterdir()):
        if not REPORT_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file(): continue
        label = 'Conversion recipe (JSON)' if path.name == 'conversion.json' else 'Audio measurements' + (' (JSON)' if path.suffix == '.json' else ' (text)')
        result.append(ReportArtifact(path.name, label))
    return result


def read_report(generation, artifact):
    if not REPORT_NAME.fullmatch(artifact.name): raise media.Error('Unknown report.')
    folder = directory(generation)
    path = folder/artifact.name
    if path.is_symlink() or path.resolve().parent != folder: raise media.Error('Invalid report path.')
    with path.open('rb') as file:
        content = file.read(REPORT_LIMIT + 1)
    if len(content) > REPORT_LIMIT: raise media.Error('Report is too large to display.')
    return {'name': artifact.name, 'label': artifact.label, 'content': content.decode('utf-8')}

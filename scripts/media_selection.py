"""Explicit source selection shared by the performance media tools."""
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path

import performance_audio as media


class SelectionOrigin(str, Enum):
    FINDER = 'Finder selection'
    PICKER = 'File picker'
    ARGUMENTS = 'Provided file paths'


@dataclass(frozen=True)
class SelectionInspection:
    details: list
    signatures: list


@dataclass(frozen=True)
class MediaSelection:
    files: tuple
    origin: SelectionOrigin

    def inspect(self):
        result, signatures = [], []
        for path in self.files:
            before = media.fingerprint(path)
            info = media.probe(path)
            video = media.stream(info, 'video')
            if media.fingerprint(path) != before:
                raise media.Error(f'{path.name} changed while reading its details. Choose the file again.')
            result.append({'name': path.name, 'path': str(path), 'size_bytes': before['size'],
                           'duration_seconds': media.duration(info, video),
                           'hdr': media.has_dolby_vision(info) or video.get('color_transfer') in ('arib-std-b67', 'smpte2084')})
            signatures.append(before)
        return SelectionInspection(result, signatures)


def select_media(use_finder=False, paths=None):
    if paths:
        return MediaSelection(tuple(Path(p).expanduser().resolve() for p in paths), SelectionOrigin.ARGUMENTS)
    script = '''ObjC.import('Foundation');
const app=Application.currentApplication();app.includeStandardAdditions=true;
let paths=[],origin='Finder selection';
if(USE_FINDER){const finder=Application('com.apple.finder');
 if(finder.running())paths=finder.selection().map(f=>ObjC.unwrap($.NSURL.URLWithString(f.url()).path));
 paths=paths.filter(p=>/\\.(mov|mp4|m4v)$/i.test(p));}
if(!paths.length){origin='File picker';try{
 paths=app.chooseFile({withPrompt:'Choose videos — review before processing',ofType:['public.movie'],multipleSelectionsAllowed:true}).map(p=>p.toString());
}catch(e){if(e.errorNumber===-128)paths=[];else throw e;}}
JSON.stringify({paths,origin});'''.replace('USE_FINDER', 'true' if use_finder else 'false')
    value = json.loads(media.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', script]))
    return MediaSelection(tuple(Path(p).resolve() for p in value['paths']), SelectionOrigin(value['origin']))

"""Private interval-session records and descriptor-anchored local storage."""
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

sys.dont_write_bytecode = True
import conversion_storage
import interval_result
import performance_audio as media
from interval_takes import Recording, RecordingKind, recording_extension

SESSIONS_DIRNAME = 'interval-analysis/sessions'
TITLE_LIMIT, NOTES_LIMIT = 120, 5000
ID_PATTERN = r'^[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,40}$'
SESSION_LIMIT_BYTES = 8 * 1024 * 1024
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def session_id(value):
    if not isinstance(value, str) or not re.fullmatch(ID_PATTERN, value):
        raise media.Error('Invalid session id.')
    return value


def session_text(title, notes):
    if not isinstance(title, str) or len(title) > TITLE_LIMIT:
        raise media.Error(f'Title must be text of at most {TITLE_LIMIT} characters.')
    if not isinstance(notes, str) or len(notes) > NOTES_LIMIT:
        raise media.Error(f'Notes must be text of at most {NOTES_LIMIT} characters.')
    return title, notes


@dataclass(frozen=True)
class IntervalSession:
    id: str
    title: str
    notes: str
    created: str
    recordings: dict[RecordingKind, Recording]
    analysis: interval_result.AnalysisResult | None

    def wire(self):
        return {'id': self.id, 'title': self.title, 'notes': self.notes, 'created': self.created,
                'recordings': {kind.value: record.wire() for kind, record in self.recordings.items()},
                'analysis': self.analysis.wire() if self.analysis is not None else None}

    def summary(self):
        return {'id': self.id, 'title': self.title, 'created': self.created,
                'duration': self.recordings[RecordingKind.REFERENCE].wire()['duration'],
                'interval': self.analysis.interval_summary() if self.analysis is not None else None}

    @classmethod
    def parse(cls, value, id):
        session_id(id)
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__) or value.get('id') != id:
            raise media.Error('Invalid session record.')
        title, notes = session_text(value['title'], value['notes'])
        try:
            created = datetime.fromisoformat(value['created'])
        except (TypeError, ValueError) as error:
            raise media.Error('Invalid session creation date.') from error
        if created.utcoffset() is None:
            raise media.Error('Session creation date must include a time zone.')
        raw = value['recordings']
        if not isinstance(raw, dict) or set(raw) != {kind.value for kind in RecordingKind}:
            raise media.Error('A session requires both recordings.')
        recordings = {kind: Recording.parse(raw[kind.value]) for kind in RecordingKind}
        if any(record.kind != kind for kind, record in recordings.items()):
            raise media.Error('Recording kind does not match the session slot.')
        if len({record.file for record in recordings.values()}) != len(RecordingKind):
            raise media.Error('Session recordings must use distinct files.')
        return cls(id, title, notes, value['created'], recordings, interval_result.parse(value['analysis']))


def open_regular(directory, name):
    descriptor = os.open(name, FILE_FLAGS, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise media.Error('Session data must be a regular file.')
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


class SessionStore:
    def __init__(self, root):
        # Do not resolve: that would silently accept a symlink in the root slot.
        self.root = Path(os.path.abspath(Path(root).expanduser()))
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self.root_directory():
            pass

    @contextmanager
    def root_directory(self):
        descriptor = os.open(self.root, DIRECTORY_FLAGS)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise media.Error('Session root must be a real directory.')
            yield descriptor
        finally:
            os.close(descriptor)

    @contextmanager
    def session_directory(self, root, id):
        descriptor = os.open(session_id(id), DIRECTORY_FLAGS, dir_fd=root)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise media.Error('Session must be a real directory.')
            yield descriptor
        finally:
            os.close(descriptor)

    def read_session(self, directory, id):
        with open_regular(directory, 'session.json') as source:
            if os.fstat(source.fileno()).st_size > SESSION_LIMIT_BYTES:
                raise media.Error('Session metadata is too large.')
            try:
                value = json.loads(source.read(SESSION_LIMIT_BYTES + 1))
            except (ValueError, RecursionError) as error:
                raise media.Error(f'Invalid session JSON: {error}') from error
        session = IntervalSession.parse(value, id)
        for recording in session.recordings.values():
            with open_regular(directory, recording.file) as source:
                if os.fstat(source.fileno()).st_size != recording.size_bytes:
                    raise media.Error('Saved recording size does not match its metadata.')
        return session

    def list(self):
        result = []
        errors = (media.Error, OSError, ValueError, TypeError, KeyError)
        try:
            with self.root_directory() as root:
                for id in os.listdir(root):
                    try:
                        with self.session_directory(root, id) as directory:
                            result.append(self.read_session(directory, id).summary())
                    except errors as error:
                        result.append({'id': id, 'error': str(error)})
        except errors as error:
            result.append({'id': '', 'error': str(error)})
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        return sorted(result, key=lambda item: (
            'error' not in item, datetime.fromisoformat(item['created'])
            if 'error' not in item else oldest), reverse=True)

    def load(self, id):
        with self.root_directory() as root, self.session_directory(root, id) as directory:
            return self.read_session(directory, id)

    def open_recording(self, id, kind):
        kind = RecordingKind(kind)
        with self.root_directory() as root, self.session_directory(root, id) as directory:
            session = self.read_session(directory, id)
            recording = session.recordings[kind]
            source = open_regular(directory, recording.file)
            if os.fstat(source.fileno()).st_size != recording.size_bytes:
                source.close()
                raise media.Error('Saved recording size does not match its metadata.')
            return source, recording

    def new_directory(self, root, title, now):
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40] or 'take'
        prefix = now.strftime('%Y%m%d-%H%M%S-')
        number = 1
        while True:
            suffix = f'-{number}' if number > 1 else ''
            id = prefix + slug[:40 - len(suffix)] + suffix
            try:
                os.mkdir(id, mode=0o700, dir_fd=root)
                return id
            except FileExistsError:
                number += 1

    def copy_take(self, directory, take):
        record = Recording.parse(take.recording.wire())
        name = f'{record.kind.value}.{recording_extension(record.mime)}'
        with open_regular(None, str(take.path)) as source:
            if os.fstat(source.fileno()).st_size != record.size_bytes:
                raise media.Error('Take changed before it could be saved.')
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                 | os.O_CLOEXEC, 0o600, dir_fd=directory)
            with os.fdopen(descriptor, 'wb') as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
                if os.fstat(output.fileno()).st_size != record.size_bytes:
                    raise media.Error('Take changed while saving.')
        return replace(record, file=name)

    def publish_session(self, directory, session, exclusive):
        conversion_storage.write_json('session.json', session.wire(),
                                      exclusive=exclusive, dir_fd=directory)

    def save(self, title, notes, takes, analysis):
        title, notes = session_text(title, notes)
        analysis = interval_result.parse(analysis)
        if not isinstance(takes, dict) or set(takes) != set(RecordingKind):
            raise media.Error('Both takes are required to save a session.')
        for kind, take in takes.items():
            if take.recording.kind != kind:
                raise media.Error('Take kind does not match its slot.')
        now = datetime.now().astimezone()
        with self.root_directory() as root:
            id = self.new_directory(root, title, now)
            with self.session_directory(root, id) as directory:
                recordings = {kind: self.copy_take(directory, takes[kind]) for kind in RecordingKind}
                session = IntervalSession(id, title, notes, now.isoformat(), recordings, analysis)
                session = IntervalSession.parse(session.wire(), id)
                self.publish_session(directory, session, exclusive=True)
                return session

    def update(self, id, title, notes):
        title, notes = session_text(title, notes)
        with self.root_directory() as root, self.session_directory(root, id) as directory:
            session = replace(self.read_session(directory, id), title=title, notes=notes)
            self.publish_session(directory, session, exclusive=False)
            return session

    def reveal(self, id):
        with self.root_directory() as root, self.session_directory(root, id):
            media.run(['/usr/bin/open', '-R', self.root / id])

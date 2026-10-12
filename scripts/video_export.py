"""Resize/remux without tone mapping, frame-rate conversion or audio encoding."""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
from pathlib import Path

import convert_video as video
import performance_audio as media

COLOR_FIELDS = ('color_range', 'color_space', 'color_transfer', 'color_primaries')
DOVI_FIELDS = ('dv_profile', 'dv_bl_signal_compatibility_id', 'rpu_present_flag',
               'el_present_flag', 'bl_present_flag')


@dataclass(frozen=True)
class ExportPlan:
    video: dict
    audio: dict | None
    width: int
    height: int
    extension: str
    copy_video: bool
    encoder: str


def dolby_record(track):
    return next((side for side in track.get('side_data_list', [])
                 if side.get('side_data_type') == 'DOVI configuration record'), None)


def plan_export(info, options, audio_track=0):
    source = media.stream(info, 'video')
    audio = media.stream(info, 'audio', audio_track) if any(s['codec_type'] == 'audio' for s in info['streams']) else None
    width, height = video.target_size(source, options.resolution)
    copy = options.resolution == 'original' or (width, height) == video.display_size(source)
    hdr = source.get('color_transfer') in ('arib-std-b67', 'smpte2084')
    dovi = dolby_record(source)
    extension = options.container if options.container != 'auto' else ('mov' if audio and audio.get('codec_name', '').startswith('pcm_') and not dovi else 'mp4')
    if dovi and extension != 'mp4':
        raise media.Error('Choose MP4 to preserve Dolby Vision metadata; MOV is not supported for that combination.')
    if not copy:
        if source.get('codec_name') not in ('h264', 'hevc'):
            raise media.Error('Resize only supports H.264/HEVC sources. Choose SDR sharing for other codecs.')
        if source.get('field_order') not in (None, 'unknown', 'progressive'):
            raise media.Error('Resize only does not deinterlace. Choose SDR sharing for interlaced video.')
        if dovi and (dovi.get('dv_profile') != 8 or dovi.get('el_present_flag') or dovi.get('dv_bl_signal_compatibility_id') not in (1, 4)):
            raise media.Error('Resize only supports single-layer Dolby Vision profile 8 HDR sources.')
    encoder = 'libx265' if source.get('codec_name') == 'hevc' or hdr else 'libx264'
    return ExportPlan(source, audio, width, height, extension, copy, encoder)


def encoding_arguments(plan):
    if plan.copy_video: return ['-c:v', 'copy']
    source = plan.video
    args = ['-vf', f'scale={plan.width}:{plan.height}:flags=lanczos,setsar=1',
            '-c:v', plan.encoder, '-preset', 'fast', '-crf', '22',
            '-pix_fmt', source['pix_fmt'], '-fps_mode:v', 'passthrough',
            '-enc_time_base:v', 'demux', '-metadata:s:v:0', 'rotate=0']
    if plan.encoder == 'libx265':
        args += ['-tag:v', 'hvc1', '-maxrate', '20M', '-bufsize', '40M',
                 '-x265-params', 'log-level=error:pools=2']
        if dolby_record(source): args += ['-dolbyvision', '1']
    for field, option in [('color_primaries', '-color_primaries'), ('color_transfer', '-color_trc'),
                          ('color_space', '-colorspace'), ('color_range', '-color_range')]:
        if source.get(field) not in (None, 'unknown', 'unspecified'):
            args += [option, str(source[field])]
    return args


def validate_export(before, plan, after):
    old, new = plan.video, media.stream(after, 'video')
    if plan.copy_video:
        media.validate_video(before, after, int(plan.audio is not None))
    else:
        if video.display_size(new) != (plan.width, plan.height) or media.rotation(new) != 0:
            raise media.Error('Resized video dimensions/orientation are incorrect.')
        for key in (*COLOR_FIELDS, 'pix_fmt'):
            if old.get(key) not in (None, 'unknown', 'unspecified') and old[key] != new.get(key):
                raise media.Error(f'Resize changed {key}; no output was published.')
        old_dovi, new_dovi = dolby_record(old), dolby_record(new)
        if old_dovi and (not new_dovi or any(old_dovi.get(k) != new_dovi.get(k) for k in DOVI_FIELDS)):
            raise media.Error('Resize did not preserve Dolby Vision HDR signaling.')
        for side in old.get('side_data_list', []):
            if side.get('side_data_type') in ('Mastering display metadata', 'Content light level metadata', 'Ambient viewing environment') and side not in new.get('side_data_list', []):
                raise media.Error('Resize did not preserve static HDR metadata.')
        print('Validating original frame timing…', flush=True)
        validate_frame_timing(Path(before['format']['filename']), Path(after['format']['filename']))
        if abs(media.duration(before, old)-media.duration(after, new)) > .12:
            raise media.Error('Resize changed video duration.')
    actual = [s for s in after['streams'] if s['codec_type'] == 'audio']
    if len(actual) != int(plan.audio is not None): raise media.Error('Unexpected output audio tracks.')
    if plan.audio:
        for key in ('codec_name', 'channels', 'sample_rate'):
            if plan.audio.get(key) != actual[0].get(key): raise media.Error(f'Output audio {key} changed.')
        if abs(media.duration(before, plan.audio)-media.duration(after, actual[0])) > .12:
            raise media.Error('Output audio duration changed.')
        old_offset = float(plan.audio.get('start_time', 0))-float(old.get('start_time', 0))
        new_offset = float(actual[0].get('start_time', 0))-float(new.get('start_time', 0))
        if abs(old_offset-new_offset) > .06: raise media.Error('Output synchronization changed.')


def frame_times(path):
    data = json.loads(media.run([media.tool('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
        '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', path]))
    frames = data.get('frames', [])
    if not frames or any('best_effort_timestamp_time' not in frame for frame in frames):
        raise media.Error('Cannot verify frame timing for this file.')
    times = [float(frame['best_effort_timestamp_time']) for frame in frames]
    if not all(math.isfinite(t) for t in times): raise media.Error('Invalid frame timing.')
    return times


def validate_frame_timing(source, output):
    # Container counts can include hidden edit-list/preroll frames on iPhone MOVs.
    # Compare the actual displayed timeline, including variable frame intervals.
    old, new = frame_times(source), frame_times(output)
    if len(old) != len(new): raise media.Error('Resize changed the displayed frame count.')
    if any(abs((a-old[0])-(b-new[0])) > .002 for a,b in zip(old,new)):
        raise media.Error('Resize changed frame timing.')


def export(source, options, output_dir=None, audio_track=0, on_published=None):
    source = source.expanduser().resolve()
    before_stat, info = media.fingerprint(source), media.probe(source)
    plan = plan_export(info, options, audio_track)
    action = 'copy original video' if plan.copy_video else f'resize to {plan.width} × {plan.height}; preserve colors and timing'
    print(f'{source.name}: {action}; copy selected audio unchanged', flush=True)
    args = ['-copyts', '-start_at_zero', '-i', source, '-map', f"0:{plan.video['index']}"]
    if plan.audio: args += ['-map', f"0:{plan.audio['index']}", '-c:a', 'copy']
    args += encoding_arguments(plan) + media.mov_options()
    destination = (output_dir or source.parent)/f'{source.stem}-{options.resolution}.{plan.extension}'
    def validate(result):
        validate_export(info, plan, result)
        if media.fingerprint(source) != before_stat: raise media.Error('Source changed during export.')
    result = media.publish(args, destination, validate, numbered=True, preserve_completed=True, on_published=on_published)
    print(f'Created: {result}', flush=True)
    return result

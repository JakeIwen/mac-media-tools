"""Validated custom-conversion wire settings and saved-preset contract."""
from dataclasses import asdict, dataclass
import copy
import math

import performance_audio as media
import performance_audio_effects as effects
from media_formats import CONTAINERS

VIDEO_MODES = {"none": "None — preserve original video", "sdr": "SDR sharing",
               "adjust": "SDR + picture adjustments", "resize": "Resize only"}
PRESERVATION_MODES = frozenset(('none', 'resize'))
TONE_MAPS = {"hable": "Hable (gentler highlights)", "mobius": "Mobius (existing converter)",
             "reinhard": "Reinhard"}
PICTURE_RANGES = {"brightness": (-.25, .25), "contrast": (.5, 1.5), "saturation": (0, 2)}


@dataclass(frozen=True)
class VideoSettings:
    mode: str = "none"
    resolution: str = "original"
    tone_map: str = "hable"
    brightness: float = 0
    contrast: float = 1
    saturation: float = 1
    container: str = 'auto'

    @classmethod
    def parse(cls, value):
        if isinstance(value, dict): value = {'container': 'auto', **value}
        if not isinstance(value, dict) or set(value) != set(asdict(cls())):
            raise media.Error("Invalid video settings.")
        if not isinstance(value['mode'], str) or not isinstance(value['tone_map'], str):
            raise media.Error("Video filter and tone mapping must be named choices.")
        if value['mode'] not in VIDEO_MODES or value['tone_map'] not in TONE_MAPS:
            raise media.Error("Unknown video filter or tone mapping.")
        if not isinstance(value['container'], str) or value['container'] not in CONTAINERS:
            raise media.Error("Unknown output file format.")
        if value['resolution'] not in ('original', '1080p', '720p', '540p'):
            raise media.Error("Unknown resolution.")
        for key, (low, high) in PICTURE_RANGES.items():
            number = value[key]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
                raise media.Error(f"Invalid {key}.")
        if value['mode'] == 'none' and value['resolution'] != 'original':
            raise media.Error("None preserves original resolution; choose Resize only or SDR to resize.")
        return cls(**value)


@dataclass(frozen=True)
class ConversionSettings:
    audio: dict
    video: VideoSettings
    audio_enabled: bool = True
    share: bool = False

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != {'audio', 'video', 'audio_enabled', 'share'}:
            raise media.Error("Invalid conversion settings.")
        for key in ('audio_enabled', 'share'):
            if type(value[key]) is not bool: raise media.Error(f"Invalid {key}.")
        preset = copy.deepcopy(value['audio'])
        effects.validate_presets({'default': 'custom', 'presets': {'custom': preset}})
        if len(preset['label']) > 100: raise media.Error("Preset name must be at most 100 characters.")
        video = VideoSettings.parse(value['video'])
        return cls(preset, video, value['audio_enabled'], value['share'] or video.mode not in PRESERVATION_MODES)

    def wire(self):
        return asdict(self)

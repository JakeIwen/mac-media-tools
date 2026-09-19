#!/usr/bin/env python3
"""Build a Finder droplet locally; no administrator access or pip packages needed."""

import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def main():
    macbook = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=macbook / "build" / "Performance Audio.app")
    parser.add_argument("--bundle-tools", type=Path,
                        help="directory containing standalone ffmpeg and ffprobe binaries")
    args = parser.parse_args()
    app = args.output.expanduser().absolute()
    if app.suffix != ".app" or app.exists():
        parser.error("Output must be a new .app path; existing apps are never overwritten.")
    if args.bundle_tools:
        for name in ("ffmpeg", "ffprobe"):
            executable = args.bundle_tools.resolve() / name
            subprocess.run([str(executable), "-version"], check=True, capture_output=True)
    app.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/usr/bin/osacompile", "-o", str(app),
                    str(macbook / "applescript" / "performance_audio.applescript")], check=True)
    resources = app / "Contents" / "Resources"
    shutil.copy2(macbook / "scripts" / "performance_audio.py", resources)
    shutil.copy2(macbook / "scripts" / "performance_audio_effects.py", resources)
    shutil.copy2(macbook / "scripts" / "convert_video.py", resources)
    shutil.copy2(macbook / "scripts" / "bpm_over_time.py", resources)
    shutil.copy2(macbook / "scripts" / "bpm_report_template.html", resources)
    (resources / "bpm-python-path.txt").write_text(str(macbook / "build/bpm-venv/bin/python") + "\n")
    shutil.copy2(macbook / "scripts" / "performance_audio_presets.json", resources)
    # Keep user-editable presets outside the signed app bundle.
    (resources / "presets-path.txt").write_text(
        str(macbook / "scripts" / "performance_audio_presets.json") + "\n")
    (resources / "python-path.txt").write_text(sys.executable + "\n")
    if args.bundle_tools:
        binaries = resources / "bin"
        binaries.mkdir()
        for name in ("ffmpeg", "ffprobe"):
            shutil.copy2(args.bundle_tools / name, binaries / name)
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as file:
        plist = plistlib.load(file)
    plist.update(CFBundleIdentifier="com.jacobr.performance-audio",
                 CFBundleName="Performance Audio", CFBundleShortVersionString="1.8",
                 NSHighResolutionCapable=True)
    with plist_path.open("wb") as file:
        plistlib.dump(plist, file)
    # Changing bundle resources invalidates osacompile's signature on newer macOS.
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
    print(f"Built: {app}\nOpen it, or drag performance videos onto it in Finder.")


if __name__ == "__main__":
    main()

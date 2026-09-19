#!/usr/bin/env python3
"""Install the BPM analyzer's dependencies into a repository-local environment."""
from pathlib import Path
import subprocess
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Use Homebrew Python: /opt/homebrew/bin/python3 macbook/scripts/setup_bpm_analysis.py")
root = Path(__file__).resolve().parents[1]
environment = root / "build" / "bpm-venv"
if not (environment / "bin/python").exists():
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(environment)], check=True)
subprocess.run([str(environment / "bin/python"), "-m", "pip", "install", "--disable-pip-version-check",
                "-r", str(Path(__file__).with_name("bpm-requirements.txt"))], check=True)
print(f"BPM runtime ready: {environment / 'bin/python'}")

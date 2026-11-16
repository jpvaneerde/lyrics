from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def setup_env() -> None:
    # pythonw has no console: give tqdm & friends somewhere to write
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    # allow a portable ffmpeg in <repo>/bin
    local_bin = REPO_ROOT / "bin"
    if local_bin.is_dir():
        os.environ["PATH"] = str(local_bin) + os.pathsep + os.environ.get("PATH", "")


def check_ffmpeg() -> str | None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        return (f"{' and '.join(missing)} not found. Install with "
                f"'winget install Gyan.FFmpeg' (then restart the app) "
                f"or drop ffmpeg.exe + ffprobe.exe into {REPO_ROOT / 'bin'}.")
    return None

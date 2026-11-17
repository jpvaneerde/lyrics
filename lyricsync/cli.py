"""Command line: python -m lyricsync <folder> [options]"""
from __future__ import annotations

import argparse
from pathlib import Path

from .batch import Options, process, scan
from .util import check_ffmpeg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lyricsync", description="Sync plain lyrics to audio -> .lrc")
    ap.add_argument("folder", type=Path)
    ap.add_argument("-m", "--model", choices=["small", "medium", "large"], default="medium")
    ap.add_argument("-l", "--language", default="auto", help="ISO code (en, nl, sv…) or auto")
    ap.add_argument("--no-separate", action="store_true", help="skip vocal isolation")
    ap.add_argument("-w", "--words", action="store_true", help="word-level (enhanced) LRC")
    ap.add_argument("--backup", action="store_true", help="with --overwrite: keep the old .lrc as .lrc.bak")
    ap.add_argument("-f", "--overwrite", action="store_true", help="redo files that already have .lrc")
    ap.add_argument("--no-recursive", action="store_true")
    ap.add_argument("--no-structure", action="store_true",
                    help="don't fix missing/misplaced choruses from the audio")
    a = ap.parse_args(argv)

    err = check_ffmpeg()
    if err:
        print(err)
        return 2

    jobs = scan(a.folder, recursive=not a.no_recursive)
    todo = [j for j in jobs if j.n_lines and (a.overwrite or not j.has_lrc)]
    print(f"{len(jobs)} audio files, {len(todo)} to sync")
    if not todo:
        return 0

    from .engine import Engine

    eng = Engine()
    print(f"Device: {eng.device_name}")
    opts = Options(a.model, None if a.language == "auto" else a.language,
                   not a.no_separate, a.words, a.overwrite, not a.no_structure, a.backup)
    failed = 0
    for i, job in enumerate(todo, 1):
        r = process(eng, job, opts, log=lambda m: None)
        conf = f" conf={r.confidence:.2f}" if r.confidence is not None else ""
        print(f"[{i}/{len(todo)}] {job.audio.name}: {r.status} {r.language}{conf} "
              f"({r.seconds:.1f}s) {'; '.join(r.warnings)}")
        if r.structure.startswith("structure fixed"):
            print(f"      {r.structure}")
        failed += not r.ok
    return 1 if failed else 0

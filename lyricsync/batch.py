"""Folder scanning and per-file processing, shared by the GUI and the CLI."""
from __future__ import annotations

import os
import re
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .lrc import build_lrc
from .lyrics_source import AUDIO_EXTS, find_lyrics, read_meta
from .structure import describe, flat_lines, restructure


@dataclass
class Options:
    model: str = "medium"
    language: str | None = None  # None = auto-detect
    separate: bool = True
    words: bool = False
    overwrite: bool = False
    structure: bool = True  # fix missing / misplaced choruses from the audio
    backup: bool = False  # when overwriting, keep the old .lrc as .lrc.bak (.lrc.bak2, ...)


@dataclass
class Job:
    audio: Path
    lrc: Path
    lyrics_source: str = ""
    n_lines: int = 0
    has_lrc: bool = False
    lrc_lines: int = -1  # lyric lines in the existing .lrc (-1: there is none, 0: it is empty)
    review: bool = False  # set by process(): the result needs a look

    @property
    def usable_lrc(self) -> bool:
        """An .lrc with lyrics in it (an empty one counts as missing)."""
        return self.has_lrc and self.lrc_lines != 0

    @property
    def empty_lrc(self) -> bool:
        return self.has_lrc and self.lrc_lines == 0

    @property
    def ready(self) -> bool:
        return self.n_lines > 0 and not self.usable_lrc

    @property
    def category(self) -> str:
        """no lyrics | empty lrc | no lrc | has lrc"""
        if self.empty_lrc:
            return "empty lrc"
        if self.has_lrc:
            return "has lrc"
        return "no lrc" if self.n_lines else "no lyrics"


@dataclass
class Result:
    status: str
    ok: bool = False
    language: str = ""
    confidence: float | None = None
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    structure: str = ""


_LYRIC_LINE = re.compile(r"^\s*\[\d+:\d+(?:[.:]\d+)?\]\s*(?:<[^>]*>\s*)*(\S.*)?$")


def count_lrc_lines(lrc: Path) -> int:
    """Lyric lines in an .lrc file: lines with a time and some text (tags like [ar:..] and empty lines don't count)."""
    try:
        text = lrc.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return 0
    n = 0
    for ln in text.splitlines():
        m = _LYRIC_LINE.match(ln)
        if m and re.sub(r"<[^>]*>", "", m.group(1) or "").strip():
            n += 1
    return n


def scan(folder: Path, recursive: bool = True) -> list[Job]:
    it = folder.rglob("*") if recursive else folder.glob("*")
    jobs = []
    for p in sorted(it, key=lambda x: str(x).lower()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            stanzas, src = find_lyrics(p)
            lrc = p.with_suffix(".lrc")
            has = lrc.exists()
            jobs.append(Job(p, lrc, src, len(flat_lines(stanzas)), has, count_lrc_lines(lrc) if has else -1))
    return jobs


def backup_path(lrc: Path) -> Path:
    """song.lrc.bak, or song.lrc.bak2, .bak3 ... when earlier backups exist (never replaces one)."""
    p = lrc.with_name(lrc.name + ".bak")
    n = 2
    while p.exists():
        p = lrc.with_name(f"{lrc.name}.bak{n}")
        n += 1
    return p


def process(engine, job: Job, opts: Options, log: Callable[[str], None] = print) -> Result:
    if job.usable_lrc and not opts.overwrite:
        return Result("skipped: .lrc exists")
    stanzas, src = find_lyrics(job.audio)  # re-read: the .txt may have been edited
    if not stanzas:
        return Result("skipped: no lyrics")

    t0 = time.time()
    note = ""
    try:
        audio = engine.load_audio(job.audio, opts.separate)
        lang = opts.language or engine.detect_language(audio, opts.model)
        if not opts.language:
            log(f"  language: {lang}")
        if opts.structure:
            heard = engine.transcribe(audio, opts.model, lang)
            stanzas, note = restructure(stanzas, heard)
            log(f"  {note}")
        else:
            note = f"lyrics order: {describe(stanzas)}"
        segs = engine.align(audio, flat_lines(stanzas), opts.model, lang)
    except Exception as e:
        log(traceback.format_exc())
        if "out of memory" in str(e).lower():
            engine.free_whisper()
            return Result("error: GPU out of memory (try a smaller model)")
        return Result(f"error: {e}")

    probs = [w["p"] for s in segs for w in s["words"] if w.get("p") is not None]
    conf = sum(probs) / len(probs) if probs else None
    warnings = []
    collapsed = sum(1 for s in segs if s["end"] - s["start"] < 0.15)
    if segs and collapsed / len(segs) > 0.2:
        warnings.append(f"{collapsed}/{len(segs)} lines got (almost) no duration")
    if conf is not None and conf < 0.35:
        warnings.append("low confidence — lyrics may not match the recording")

    text = build_lrc(segs, words=opts.words, meta=read_meta(job.audio))
    tmp = job.lrc.with_name(job.lrc.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if opts.backup and job.lrc.exists() and job.lrc.stat().st_size > 0:
        shutil.copy2(job.lrc, backup_path(job.lrc))
    os.replace(tmp, job.lrc)
    job.has_lrc = True
    job.lrc_lines = len([s for s in segs if s["text"]])
    job.review = bool(warnings)

    status = "done, check it" if warnings else "done"
    if note.startswith("structure fixed"):
        status += " (structure fixed)"
    return Result(status, True, lang, conf, time.time() - t0, warnings, note)

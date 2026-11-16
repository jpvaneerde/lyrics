"""Folder scanning and per-file processing, shared by the GUI and the CLI."""
from __future__ import annotations

import os
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


@dataclass
class Job:
    audio: Path
    lrc: Path
    lyrics_source: str = ""
    n_lines: int = 0
    has_lrc: bool = False

    @property
    def ready(self) -> bool:
        return self.n_lines > 0 and not self.has_lrc


@dataclass
class Result:
    status: str
    ok: bool = False
    language: str = ""
    confidence: float | None = None
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    structure: str = ""


def scan(folder: Path, recursive: bool = True) -> list[Job]:
    it = folder.rglob("*") if recursive else folder.glob("*")
    jobs = []
    for p in sorted(it, key=lambda x: str(x).lower()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            stanzas, src = find_lyrics(p)
            lrc = p.with_suffix(".lrc")
            jobs.append(Job(p, lrc, src, len(flat_lines(stanzas)), lrc.exists()))
    return jobs


def process(engine, job: Job, opts: Options, log: Callable[[str], None] = print) -> Result:
    if job.lrc.exists() and not opts.overwrite:
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
    os.replace(tmp, job.lrc)
    job.has_lrc = True

    status = "done, check it" if warnings else "done"
    if note.startswith("structure fixed"):
        status += " (structure fixed)"
    return Result(status, True, lang, conf, time.time() - t0, warnings, note)

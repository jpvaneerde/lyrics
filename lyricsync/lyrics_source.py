"""Finding and cleaning plain lyrics for an audio file."""
from __future__ import annotations

from pathlib import Path

from .structure import Stanza, flat_lines, parse_lyrics

AUDIO_EXTS = {
    ".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".ape", ".wv", ".alac",
}



def clean_lyrics(text: str) -> list[str]:
    """Plain lyrics -> list of sung lines in file order (chorus markers expanded)."""
    return flat_lines(parse_lyrics(text))


def _embedded_lyrics(path: Path) -> str | None:
    try:
        import mutagen

        f = mutagen.File(path)
    except Exception:
        return None
    if f is None or f.tags is None:
        return None
    tags = f.tags
    # ID3 (mp3, wav, aiff)
    if hasattr(tags, "getall"):
        for frame in tags.getall("USLT"):
            if str(frame.text).strip():
                return str(frame.text)
        return None
    # MP4 / M4A
    try:
        if "\xa9lyr" in tags:
            return str(tags["\xa9lyr"][0])
    except Exception:
        pass
    # Vorbis comments (flac/ogg/opus) and APEv2 — keys are case-insensitive there
    for key in ("LYRICS", "UNSYNCEDLYRICS", "UNSYNCED LYRICS"):
        try:
            v = tags.get(key)
        except Exception:
            v = None
        if v:
            v = v[0] if isinstance(v, list) else v
            if str(v).strip():
                return str(v)
    return None


def _read_text(p: Path) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="latin-1")


def find_lyrics(audio: Path) -> tuple[list[Stanza], str]:
    """Return (stanzas in file order, source). Sidecar .txt wins over embedded tags."""
    txt = audio.with_suffix(".txt")
    if txt.is_file():
        st = parse_lyrics(_read_text(txt))
        if st:
            return st, ".txt"
    emb = _embedded_lyrics(audio)
    if emb:
        st = parse_lyrics(emb)
        if st:
            return st, "tags"
    return [], ""


def read_meta(audio: Path) -> dict:
    meta: dict = {}
    try:
        import mutagen

        f = mutagen.File(audio, easy=True)
        if f is None:
            return meta
        if getattr(f, "info", None) and getattr(f.info, "length", None):
            meta["length"] = float(f.info.length)
        tags = f.tags or {}
        for src, dst in (("title", "ti"), ("artist", "ar"), ("album", "al")):
            try:
                v = tags.get(src)
            except Exception:
                v = None
            if v:
                meta[dst] = str(v[0] if isinstance(v, list) else v)
    except Exception:
        pass
    return meta

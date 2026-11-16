"""LRC writing."""
from __future__ import annotations


def fmt(t: float) -> str:
    """Seconds -> mm:ss.xx (centiseconds, rounding safely across minute borders)."""
    cs = int(round(max(t, 0.0) * 100))
    m, cs = divmod(cs, 6000)
    return f"{m:02d}:{cs // 100:02d}.{cs % 100:02d}"


def build_lrc(segments: list[dict], words: bool = False, meta: dict | None = None) -> str:
    meta = meta or {}
    out: list[str] = []
    for key in ("ti", "ar", "al"):
        if meta.get(key):
            out.append(f"[{key}:{meta[key]}]")
    if meta.get("length"):
        m, s = divmod(int(round(meta["length"])), 60)
        out.append(f"[length:{m:02d}:{s:02d}]")
    out.append("[by:LyricSync]")
    out.append("")
    for seg in segments:
        if words and seg.get("words"):
            body = " ".join(f"<{fmt(w['start'])}>{w['word'].strip()}" for w in seg["words"]
                            if w["word"].strip())
            out.append(f"[{fmt(seg['start'])}]{body}")
        else:
            out.append(f"[{fmt(seg['start'])}]{seg['text']}")
    return "\n".join(out) + "\n"

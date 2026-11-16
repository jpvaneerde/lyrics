"""Song structure: expand chorus/refrain markers in the text and fix the
section order (missing, extra or misplaced choruses) from what is actually sung.

Two layers:
1. parse_lyrics(text)     -> stanzas in file order, with [Chorus] / "Repeat chorus x2" /
                             "Chorus: first line..." references filled in.
2. restructure(stanzas, transcript)
                          -> stanza order as heard in a quick Whisper transcription
                             of the vocals; verses that can't be found are kept in place.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# label words (lower case). Multi-word / hyphenated first so the regex prefers them.
KINDS: dict[str, list[str]] = {
    "prechorus": ["pre-chorus", "pre chorus", "prechorus", "pre-refrain", "pre-refrein",
                  "pre-hook", "prehook", "pre-refräng"],
    "postchorus": ["post-chorus", "post chorus", "postchorus", "post-refrain"],
    "chorus": ["chorus", "refrain", "refrein", "refräng", "refrang", "omkväde", "kehrreim",
               "estribillo", "coro", "ritornello", "hook"],
    "verse": ["verse", "vers", "couplet", "strofe", "strophe", "strofa", "verso"],
    "bridge": ["bridge", "brug", "brygga", "pont", "puente", "middle 8", "middle eight"],
    "intro": ["intro"],
    "outro": ["outro", "coda"],
    "other": ["interlude", "instrumental", "solo", "guitar solo", "breakdown", "break",
              "drop", "spoken", "talking", "ad-libs", "ad libs", "adlibs"],
}
_WORD2KIND = {w: k for k, ws in KINDS.items() for w in ws}
_KIND_RE = "|".join(re.escape(w) for w in sorted(_WORD2KIND, key=len, reverse=True))
REPEATABLE = {"chorus", "prechorus", "postchorus", "bridge"}
SHORT = {"chorus": "C", "prechorus": "PC", "postchorus": "PoC", "verse": "V",
         "bridge": "B", "intro": "I", "outro": "O"}

_REPEAT_WORD = r"(?:repeat|herhaal|herhaling|upprepa|wiederholung|wiederholen|bis|reprise)"
_COUNT = (r"(?:[x×]\s*(\d+)|(\d+)\s*(?:[x×]|times|keer|gånger|ganger|mal|fois|veces|volte)"
          r"|(twice|2x|tweemaal|två gånger))")
_BRACKETED = re.compile(r"^\s*[\[({*]+\s*(.*?)\s*[\])}*]+\s*:?\s*(?:" + _COUNT + r")?\s*$", re.I)
_PLAIN = re.compile(
    r"^\s*(?:" + _REPEAT_WORD + r"\s+(?:the\s+|het\s+|de\s+)?)?(" + _KIND_RE + r")\b"
    r"\s*(\d+)?\s*(?:" + _COUNT + r")?\s*(?:(:)\s*(.*))?$", re.I)
_REPEAT_ONLY = re.compile(r"^\W*" + _REPEAT_WORD + r"\W*(?:" + _COUNT + r")?\W*$", re.I)
_LRC_TS = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")
_WORD_TS = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
_META = re.compile(r"^\[[a-z]{2,8}:.*\]$", re.I)  # [ar:..] [ti:..] LRC headers
_LINE_REPEAT = re.compile(r"\s*(?:\(\s*[x×]\s*(\d+)\s*\)|\(\s*(\d+)\s*[x×]\s*\)|\s[x×](\d+))\s*$",
                          re.I)
_ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…|\betc\.?|\(\s*etc\.?\s*\))\s*$", re.I)


def _count(m: re.Match | None, base: int) -> int:
    if not m:
        return 1
    g = m.groups()[base:base + 3]
    if g[0] or g[1]:
        return max(1, min(int(g[0] or g[1]), 8))
    if g[2]:
        return 2
    return 1


@dataclass
class Label:
    kind: str           # chorus / verse / ... / previous / other
    num: int | None = None
    count: int = 1
    inline: str = ""    # "Chorus: first line" -> "first line"


def parse_label(line: str) -> Label | None:
    if _META.match(line):
        return Label("other")
    if _REPEAT_ONLY.match(line):
        m = re.search(_COUNT, line, re.I)
        return Label("previous", count=_count(m, 0) if m else 1)
    m = _BRACKETED.match(line)
    if m:
        inner = m.group(1).strip()
        outer_count = _count(m, 1)
        if not inner:
            return Label("other")
        pm = _PLAIN.match(inner.split(":")[0].strip()) or _PLAIN.match(inner)
        if pm:
            num = int(pm.group(2)) if pm.group(2) else None
            cnt = max(outer_count, _count(pm, 2))
            return Label(_WORD2KIND[pm.group(1).lower()], num, cnt)
        if _REPEAT_ONLY.match(inner):
            return Label("previous", count=outer_count)
        # "(oh oh oh)" is a sung backing vocal, keep it; "[Spoken]" / "[Artist]" is a label
        if m.group(0).lstrip().startswith("("):
            return None
        return Label("other")
    pm = _PLAIN.match(line)
    if pm:
        rest = (pm.group(7) or "").strip()
        has_colon = bool(pm.group(6))
        tail = line[pm.end(1):].strip()
        # "Hook me up baby" is lyrics, not a label: require nothing after, a number/count, or a colon
        if not has_colon and tail and not re.fullmatch(r"\d+|\s*" + _COUNT + r"|\d+\s*" + _COUNT,
                                                       tail, re.I):
            return None
        num = int(pm.group(2)) if pm.group(2) else None
        return Label(_WORD2KIND[pm.group(1).lower()], num, _count(pm, 2), rest)
    return None


# ---------------------------------------------------------------------------
@dataclass
class Stanza:
    lines: list[str]
    kind: str | None = None
    num: int | None = None
    tag: str = ""          # short name for reports, e.g. V1, C, B

    @property
    def key(self) -> str:
        return " / ".join(" ".join(norm_words(l)) for l in self.lines)


@dataclass
class _Section:
    kind: str | None
    num: int | None = None
    count: int = 1
    lines: list[str] = field(default_factory=list)


def _clean_line(raw: str) -> list[str]:
    line = _WORD_TS.sub("", _LRC_TS.sub("", raw)).strip()
    if not line:
        return []
    m = _LINE_REPEAT.search(line)
    if m:
        n = max(1, min(int(m.group(1) or m.group(2) or m.group(3)), 8))
        line = line[: m.start()].strip()
        return [line] * n if line else []
    return [line]


def parse_lyrics(text: str) -> list[Stanza]:
    """Plain lyrics -> stanzas in file order with chorus references expanded."""
    sections: list[_Section] = []
    cur: _Section | None = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = _WORD_TS.sub("", _LRC_TS.sub("", raw)).strip()
        if not stripped:
            if cur is not None and cur.lines:
                cur = None
            continue
        lab = parse_label(stripped)
        if lab:
            cur = _Section(lab.kind, lab.num, lab.count)
            sections.append(cur)
            if lab.inline:
                cur.lines.extend(_clean_line(lab.inline))
            continue
        if cur is None:
            cur = _Section(None)
            sections.append(cur)
        cur.lines.extend(_clean_line(stripped))

    # definitions: first full text for each kind (and kind+number)
    defs: dict = {}
    for s in sections:
        if s.lines and s.kind not in (None, "other", "previous") and not _is_abbrev(s.lines):
            defs.setdefault((s.kind, s.num), s.lines)
            defs.setdefault(s.kind, s.lines)

    out: list[Stanza] = []
    verse_no = 0
    for s in sections:
        lines = s.lines
        if s.kind == "previous":
            if out:
                out.extend(Stanza(list(out[-1].lines), out[-1].kind, out[-1].num, out[-1].tag)
                           for _ in range(s.count))
            continue
        if s.kind and s.kind != "other":
            ref = defs.get((s.kind, s.num)) or defs.get(s.kind)
            if ref is not None and (not lines or (_is_abbrev(lines) and _is_prefix(lines, ref))):
                lines = ref
        if not lines:
            continue
        lines = [_ELLIPSIS.sub("", l).strip() or l for l in lines]
        kind = s.kind if s.kind != "other" else None
        num = s.num
        if kind == "verse" and num is None:
            verse_no += 1
            num = verse_no
        elif kind == "verse":
            verse_no = num
        for _ in range(s.count):
            out.append(Stanza(list(lines), kind, num))
    _tag(out)
    return out


def _is_abbrev(lines: list[str]) -> bool:
    return bool(lines) and bool(_ELLIPSIS.search(lines[-1]))


def _is_prefix(short: list[str], full: list[str]) -> bool:
    if len(short) > len(full):
        return False
    for a, b in zip(short, full):
        na, nb = norm_words(_ELLIPSIS.sub("", a)), norm_words(b)
        if not na or nb[: len(na)] != na:
            return False
    return True


def _tag(stanzas: list[Stanza]) -> None:
    """Give identical stanzas identical tags; mark unlabeled repeats of the chorus as chorus."""
    by_key: dict[str, Stanza] = {}
    chorus_keys = {s.key for s in stanzas if s.kind == "chorus"}
    n_unl = 0
    for s in stanzas:
        if s.kind is None and s.key in chorus_keys:
            s.kind = "chorus"
        first = by_key.get(s.key)
        if first:
            s.tag, s.kind = first.tag, first.kind
            continue
        if s.kind:
            base = SHORT.get(s.kind, s.kind[:2].upper())
            same = [x for x in by_key.values() if x.kind == s.kind]
            s.tag = f"{base}{s.num or (len(same) + 1)}" if (s.kind == "verse" or same) else base
        else:
            n_unl += 1
            s.tag = f"S{n_unl}"
        by_key[s.key] = s


def flat_lines(stanzas: list[Stanza]) -> list[str]:
    return [l for s in stanzas for l in s.lines]


def describe(stanzas: list[Stanza]) -> str:
    return " ".join(s.tag for s in stanzas)


# ---------------------------------------------------------------------------
def norm_words(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c)).replace("'", "").replace("’", "")
    return re.findall(r"\w+", s)


@dataclass
class _Cand:
    start: int
    end: int
    weight: float
    score: float
    uid: int
    a: int  # line range within the stanza
    b: int


def restructure(stanzas: list[Stanza], transcript: str,
                min_score: float = 0.62) -> tuple[list[Stanza], str]:
    """Reorder/repeat stanzas to follow the sung transcript. Returns (stanzas, note)."""
    from rapidfuzz.fuzz import ratio

    toks = norm_words(transcript)
    if len(toks) < 15 or not stanzas:
        return stanzas, "transcript too short — kept lyrics order"

    # unique stanzas in first-appearance order
    uniq: list[Stanza] = []
    seen: dict[str, int] = {}
    file_uids: list[int] = []
    for s in stanzas:
        if s.key not in seen:
            seen[s.key] = len(uniq)
            uniq.append(s)
        file_uids.append(seen[s.key])
    times_in_file = {u: file_uids.count(u) for u in range(len(uniq))}
    repeatable = {u for u, st in enumerate(uniq)
                  if times_in_file[u] > 1 or st.kind in REPEATABLE}

    # transcript as one string with token offsets for cheap window slicing
    offs, pos = [], 0
    for t in toks:
        offs.append(pos)
        pos += len(t) + 1
    tstr = " ".join(toks)

    def window(i: int, n: int) -> str:
        j = min(i + n, len(toks))
        return tstr[offs[i]: offs[j - 1] + len(toks[j - 1])]

    cands: list[_Cand] = []
    done_targets: set = set()
    for uid, st in enumerate(uniq):
        lw = [norm_words(l) for l in st.lines]
        nl = len(lw)
        for a in range(nl):
            for b in range(a + 1, nl + 1):
                words = [w for l in lw[a:b] for w in l]
                k = len(words)
                full = (a == 0 and b == nl)
                if k < 4:
                    continue  # too short to place reliably
                target = " ".join(words)
                if (uid, target) in done_targets:
                    continue
                done_targets.add((uid, target))
                bonus = 1.3 if full else 1.0
                best_here: list[_Cand] = []
                for n in {max(1, round(k * 0.85)), k, round(k * 1.15)}:
                    for i in range(0, max(1, len(toks) - n + 1)):
                        sc = ratio(target, window(i, n)) / 100.0
                        if sc >= min_score:
                            best_here.append(_Cand(i, min(i + n, len(toks)),
                                                   (sc - 0.5) * k * bonus, sc, uid, a, b))
                # non-max suppression per unit/range
                best_here.sort(key=lambda c: -c.weight)
                kept: list[_Cand] = []
                for c in best_here:
                    if all(c.end <= o.start or c.start >= o.end for o in kept):
                        kept.append(c)
                cands.extend(kept)

    if not cands:
        return stanzas, "no lyrics recognised in the vocals — kept lyrics order"

    # weighted interval scheduling: best non-overlapping set
    cands.sort(key=lambda c: c.end)
    import bisect

    ends = [c.end for c in cands]
    best = [0.0] * (len(cands) + 1)
    take = [False] * len(cands)
    for i, c in enumerate(cands):
        j = bisect.bisect_right(ends, c.start, 0, i)
        with_c = best[j] + c.weight
        if with_c > best[i]:
            best[i + 1], take[i] = with_c, True
        else:
            best[i + 1] = best[i]
    chosen: list[_Cand] = []
    i = len(cands) - 1
    while i >= 0:
        if take[i]:
            chosen.append(cands[i])
            i = bisect.bisect_right(ends, cands[i].start, 0, i) - 1
        else:
            i -= 1
    chosen.reverse()

    # glue consecutive pieces of the same stanza (lines 1-2 + line 3 -> lines 1-3)
    merged: list[_Cand] = []
    for c in chosen:
        p = merged[-1] if merged else None
        if p and p.uid == c.uid and c.a == p.b and c.start - p.end <= 3:
            merged[-1] = _Cand(p.start, c.end, p.weight + c.weight,
                               min(p.score, c.score), p.uid, p.a, c.b)
        else:
            merged.append(c)
    chosen = merged

    matched = {c.uid for c in chosen}
    if len(matched) < max(1, 0.5 * len(uniq)):
        return stanzas, (f"only {len(matched)}/{len(uniq)} sections recognised — "
                         "kept lyrics order")

    # a stanza written once may still be sung several times (chorus written once, no label):
    # promote it when the audio clearly has it at least twice
    for uid in range(len(uniq)):
        if uid in repeatable:
            continue
        nl = len(uniq[uid].lines)
        strong = [c for c in chosen if c.uid == uid and c.score >= 0.75 and (c.b - c.a) >= 0.6 * nl]
        if len(strong) >= 2:
            repeatable.add(uid)

    # other stanzas (verses) are placed once, at their best hit
    best_hit: dict[int, _Cand] = {}
    for c in chosen:
        if c.uid not in repeatable and (c.uid not in best_hit or c.weight > best_hit[c.uid].weight):
            best_hit[c.uid] = c
    chosen = [c for c in chosen if c.uid in repeatable or best_hit[c.uid] is c]

    # build the sung sequence
    seq: list[tuple[int, int, int]] = []  # (uid, a, b)
    for c in chosen:
        nl = len(uniq[c.uid].lines)
        a, b = c.a, c.b
        if (b - a) >= 0.6 * nl or c.uid not in repeatable:
            a, b = 0, nl
        if seq and seq[-1][0] == c.uid and c.uid not in repeatable:
            continue  # two partial hits of the same verse
        seq.append((c.uid, a, b))

    # keep sections that weren't heard (e.g. badly transcribed verse) at their file position
    for uid in range(len(uniq)):
        if uid in matched:
            continue
        ins = len(seq)
        for idx, (u, _, _) in enumerate(seq):
            if u > uid:
                ins = idx
                break
        seq.insert(ins, (uid, 0, len(uniq[uid].lines)))

    out: list[Stanza] = []
    for uid, a, b in seq:
        st = uniq[uid]
        part = (a, b) != (0, len(st.lines))
        out.append(Stanza(st.lines[a:b], st.kind, st.num, st.tag + ("½" if part else "")))

    before, after = describe(stanzas), describe(out)
    if before == after:
        return out, f"structure confirmed: {after}"
    return out, f"structure fixed: {before}  →  {after}"

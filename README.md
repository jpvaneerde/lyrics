# LyricSync

Desktop app that turns plain lyrics into time-synced `.lrc` files, running locally on an NVIDIA GPU.

**How it works:** Demucs isolates the vocals → Whisper (via stable-ts) force-aligns the
known lyrics to them → one LRC line per lyric line (optionally per-word timestamps).

## Setup (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

Installs Python 3.12 and ffmpeg (winget) if missing, creates `.venv`, installs CUDA PyTorch
(RTX 50xx needs CUDA ≥ 12.8 and NVIDIA driver ≥ 570) and the rest. The last line should
show your GPU name. Models download on first use to `%USERPROFILE%\.cache`
(small 0.5 GB, medium 1.5 GB, large 3 GB, htdemucs 80 MB).

## Use

- **GUI:** `run.bat` → Browse to a folder → check files → *Sync checked*.
- **CLI:** `sync.bat "D:\Music\Album" -m large -w` (`-h` for all options).

### Where lyrics come from

For every audio file (`mp3 flac wav m4a ogg opus wma aiff ape wv`):

1. `<same name>.txt` next to the audio file, otherwise
2. embedded unsynced lyrics tags (ID3 USLT, Vorbis `LYRICS`/`UNSYNCEDLYRICS`, MP4 `©lyr`).

Cleaned automatically: section labels, existing timestamps, LRC headers; `line (x2)` is expanded.

### Choruses / refrains

Two layers, so you don't have to fix lyric files by hand:

1. **Markers in the text are filled in:** `[Chorus]`, `(Chorus x2)`, `Repeat chorus`,
   `Refrein 2x`, `[Refräng]`, `Omkväde:`, `Herhaal refrein`, `(repeat)` (= previous stanza),
   and abbreviated ones like `Chorus: Oh hold me now...` are replaced by the full chorus text.
2. **Fix chorus repeats from audio** (on by default): the vocals are transcribed quickly,
   each stanza is located in what's actually sung, and the lyrics are rebuilt in sung order —
   missing choruses are added, extra ones dropped, misplaced ones moved, half-choruses
   (`C½`) kept as half. Works even when the chorus is written only once without a label.
   Verses that can't be recognised stay at their original position; if too little is
   recognised the file order is kept. The log shows what changed, e.g.
   `structure fixed: V1 C V2 B  →  V1 C V2 C B C C½` (also as tooltip on the Status cell).

Output is `<same name>.lrc` next to the audio (written atomically; existing `.lrc` files are
skipped unless *Overwrite* is on).

### Options

| Option | |
|---|---|
| Model | `small` fast / `medium` good default / `large` (large-v3) best on mumbled or dense mixes |
| Language | `auto` detects from the loudest 30 s of vocals; set it if detection picks wrong |
| Isolate vocals | strongly recommended for real songs; off only for a cappella / spoken word |
| Fix chorus repeats | see above; adds a few seconds per song |
| Word timing | enhanced LRC (`<mm:ss.xx>` per word) for karaoke-style players |

**Status colours:** green = done, orange = done but check it (many zero-length lines or low
confidence — usually the lyrics don't match the recording), red = error.


## Overwriting

Tick **Overwrite existing .lrc** to redo songs that already have one. With **Keep old as .lrc.bak** ticked (the default) the old file is first saved next to it as `song.lrc.bak` (`.bak2`, `.bak3`… if one exists), so hand-fixed files are never lost. On the command line: `--overwrite --backup`.

## Filtering the list

The **Show** box above the table narrows the list: *No .lrc yet* (lyrics text but no `.lrc`), *No lyrics text* (no `.txt` and nothing in the tags: these need lyrics added), *Empty .lrc* (a file with no lyric lines; it is treated as missing and gets redone), and *Needs review* (songs marked "check it" in this session). **Select ready** and **Sync checked** only act on the rows that are shown.

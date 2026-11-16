"""GPU alignment engine: Demucs vocal isolation + Whisper forced alignment (stable-ts)."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Callable

import torch

MODELS = {"small": "small", "medium": "medium", "large": "large-v3"}
WHISPER_SR = 16000


class Engine:
    def __init__(self, log: Callable[[str], None] = print):
        self.log = log
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._sep = None
        self._wname: str | None = None
        self._wmodel = None

    @property
    def device_name(self) -> str:
        if self.device == "cuda":
            return torch.cuda.get_device_name(0)
        return "CPU (no CUDA GPU found — this will be slow)"

    # ---- models -----------------------------------------------------------
    def _separator(self):
        if self._sep is None:
            from demucs.pretrained import get_model

            self.log("Loading vocal separation model (htdemucs)…")
            self._sep = get_model("htdemucs").to(self.device).eval()
        return self._sep

    def _whisper(self, size: str):
        name = MODELS[size]
        if self._wname != name:
            self.free_whisper()
            import stable_whisper

            self.log(f"Loading Whisper {name} (downloads on first use)…")
            self._wmodel = stable_whisper.load_model(name, device=self.device)
            self._wname = name
        return self._wmodel

    def free_whisper(self):
        self._wmodel = None
        self._wname = None
        gc.collect()
        if self.device == "cuda":
            torch.cuda.empty_cache()

    # ---- audio ------------------------------------------------------------
    def load_audio(self, path: Path, separate: bool) -> torch.Tensor:
        """Mono 16 kHz float tensor; vocals only when separate=True."""
        from demucs.audio import AudioFile

        if not separate:
            wav = AudioFile(path).read(streams=0, samplerate=WHISPER_SR, channels=1)
            return wav.mean(0).float().cpu()

        import torchaudio
        from demucs.apply import apply_model

        sep = self._separator()
        wav = AudioFile(path).read(streams=0, samplerate=sep.samplerate,
                                   channels=sep.audio_channels)
        ref = wav.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        wav = (wav - mean) / std
        with torch.inference_mode():
            src = apply_model(sep, wav[None], device=self.device, split=True,
                              overlap=0.25, progress=False)[0]
        vocals = src[sep.sources.index("vocals")] * std + mean
        mono = vocals.mean(0).float().cpu()
        return torchaudio.functional.resample(mono, sep.samplerate, WHISPER_SR)

    # ---- language ---------------------------------------------------------
    def _detect_language(self, model, audio: torch.Tensor) -> str:
        """Detect on the loudest 30 s window (skips silent intros)."""
        try:
            import whisper

            win, hop = 30 * WHISPER_SR, 10 * WHISPER_SR
            best, best_e = 0, -1.0
            for s in range(0, max(1, audio.numel() - win + 1), hop):
                e = float(audio[s:s + win].pow(2).mean())
                if e > best_e:
                    best, best_e = s, e
            clip = whisper.pad_or_trim(audio[best:best + win])
            mel = whisper.log_mel_spectrogram(clip, n_mels=model.dims.n_mels).to(model.device)
            _, probs = model.detect_language(mel)
            if isinstance(probs, list):
                probs = probs[0]
            return max(probs, key=probs.get)
        except Exception as e:  # pragma: no cover
            self.log(f"Language detection failed ({e}); assuming English.")
            return "en"

    # ---- main steps (audio = output of load_audio) ------------------------
    def detect_language(self, audio: torch.Tensor, size: str) -> str:
        return self._detect_language(self._whisper(size), audio)

    def transcribe(self, audio: torch.Tensor, size: str, language: str) -> str:
        """Quick free transcription of the vocals, used to detect the song structure."""
        model = self._whisper(size)
        res = model.transcribe(audio, language=language, verbose=None, regroup=False,
                               condition_on_previous_text=False)
        return res.text if res is not None else ""

    def align(self, audio: torch.Tensor, lines: list[str], size: str, language: str):
        model = self._whisper(size)
        result = model.align(audio, "\n".join(lines), language=language,
                             original_split=True, verbose=None)
        if result is None or not result.segments:
            raise RuntimeError("alignment failed (lyrics probably don't match the audio)")
        segments = []
        for s in result.segments:
            segments.append({
                "start": float(s.start),
                "end": float(s.end),
                "text": s.text.strip(),
                "words": [
                    {"start": float(w.start), "end": float(w.end), "word": w.word,
                     "p": getattr(w, "probability", None)}
                    for w in (s.words or [])
                ],
            })
        return segments

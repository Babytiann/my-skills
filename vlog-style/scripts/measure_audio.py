#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = [
#   "numpy",
#   "librosa",
#   "soundfile",
#   "soxr",
#   "torch",
#   "torchaudio",
#   "beat-this",
#   "mlx-whisper; sys_platform == 'darwin' and platform_machine == 'arm64'",
# ]
# ///
# -*- coding: utf-8 -*-
"""Measure the sound of one proxied video, or beat-grid a music file.

  uv run --python 3.13 --script measure_audio.py DIR [--no-asr] [--prompt TEXT]
                                                [--language zh|en|auto] [--sentences]
  uv run --python 3.13 --script measure_audio.py --music FILE --out beats.json

DIR is a prep.py folder (asr16k.wav + music44k.wav). Writes DIR/measure_audio.json:
  loudness    ffmpeg ebur128: integrated LUFS, LRA, true peak
  speech      spans from burned-in captions (measure_video.json text), platform
              subtitles (subtitles.json) and mlx-whisper segments, merged
  transcript  captions first (more accurate than ASR), else platform, else whisper
  beats       beat_this (CPU) over the whole track, librosa when its checkpoint
              is not cached (no downbeats then); BPM reported over the
              non-speech stretches only, where cut-to-beat is judged
  tempo_check librosa beat_track, to catch a half/double-time beat_this result
  onsets      librosa onsets; strong off-beat onsets are sound-effect candidates
  music       silences, beat gaps and tempo changes (music change candidates)
  duck        music level under speech vs around it, from the 30-90 Hz band
              (speech has little energy there). An estimate, marked as such.
--music writes {beats, downbeats, bpm, duration_s} for plan_cut.py.
--sentences (apply) transcribes in pieces under 30 s with word timestamps and
regroups the words into sentences; --language auto detects each piece's
language. Without them the output is unchanged.
Claude cannot hear: every sound conclusion in a style pack comes from here.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stats  # noqa: E402

OUT = "measure_audio.json"
ASR_REPO = "mlx-community/whisper-large-v3-turbo"
ASR_PROMPT = "以下是普通话的旅行视频旁白，使用简体中文和标点。"
HOP_S = 0.05


def log(msg: str) -> None:
    print(f"[measure_audio] {msg}", file=sys.stderr, flush=True)


def rnd(x, k: int = 3):
    return None if x is None or not math.isfinite(float(x)) else round(float(x), k)


def decode(path: Path, sr: int, channels: int = 1) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
                        "-vn", "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"], capture_output=True)
    if r.returncode:
        raise SystemExit(f"ffmpeg could not decode {path.name}: {r.stderr.decode(errors='replace')[-300:]}")
    x = np.frombuffer(r.stdout, "<f4").astype(np.float32)
    return x.reshape(-1, channels) if channels > 1 else x


def loudness(path: Path) -> dict:
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]

    def grab(pat):
        m = re.search(pat, tail)
        return float(m.group(1)) if m else None
    return {"integrated_lufs": grab(r"I:\s+(-?[\d.]+) LUFS"), "lra_lu": grab(r"LRA:\s+(-?[\d.]+) LU"),
            "true_peak_dbfs": grab(r"Peak:\s+(-?[\d.]+|-inf) dBFS")}


def merge(spans, gap: float = 0.3) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for a, b in spans if b > a):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[rnd(a), rnd(b)] for a, b in out]


def complement(spans, total: float, margin: float = 0.5, min_len: float = 4.0) -> list[list[float]]:
    out, t = [], 0.0
    for a, b in spans:
        if a - margin - t >= min_len:
            out.append([rnd(t), rnd(a - margin)])
        t = max(t, b + margin)
    if total - t >= min_len:
        out.append([rnd(t), rnd(total)])
    return out


def platform_subs(d: Path) -> list[dict]:
    p = d / "subtitles.json"
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("body") or raw.get("subtitles") or raw.get("data") or []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        a = r.get("from", r.get("start", r.get("t_in")))
        b = r.get("to", r.get("end", r.get("t_out")))
        text = r.get("content", r.get("text", ""))
        try:
            out.append({"t_in": float(a), "t_out": float(b), "text": str(text)})
        except (TypeError, ValueError):
            continue
    return out


def asr_kept(s: dict) -> bool:
    return not (s.get("no_speech_prob", 0) > 0.6 or s.get("avg_logprob", 0) < -1.0
                or s.get("compression_ratio", 0) > 2.4)


def whisper(path: Path, prompt: str, language: str | None = "zh") -> list[dict]:
    import mlx_whisper

    audio = decode(path, 16000)
    with contextlib.redirect_stdout(sys.stderr):
        res = mlx_whisper.transcribe(audio, path_or_hf_repo=ASR_REPO, language=language, initial_prompt=prompt,
                                     condition_on_previous_text=False, verbose=None)
    segs = []
    for s in res.get("segments", []):
        if not asr_kept(s):
            continue
        text = s.get("text", "").strip()
        if text:
            segs.append({"t_in": rnd(s["start"]), "t_out": rnd(s["end"]), "text": text})
    return segs


def whisper_sentences(path: Path, prompt: str, language: str | None = "zh") -> list[dict]:
    """Sentences for apply: pieces under 30 s, word timestamps, regrouped by stats.sentences.

    With one long call the prompt (simplified Chinese, punctuation, names) only
    reaches the first 30 s window; Test A on a 29 min reference got punctuation
    in 24 of 1121 segments. Pieces are cut at quiet moments so each gets the
    prompt. language None detects per piece, so English talk comes back as
    English (marked with `lang`) instead of being forced into Chinese.
    """
    import mlx_whisper

    sr = 16000
    audio = decode(path, sr)
    cuts = stats.quiet_cuts(envelope_db(audio, sr).tolist(), HOP_S)
    edges = [0.0, *cuts, len(audio) / sr]
    def run(piece, lang, text):
        with contextlib.redirect_stdout(sys.stderr):
            return mlx_whisper.transcribe(piece, path_or_hf_repo=ASR_REPO, language=lang, initial_prompt=text,
                                          condition_on_previous_text=False, verbose=None, word_timestamps=True)

    words = []
    for a, b in zip(edges, edges[1:]):
        piece = audio[int(a * sr):int(b * sr)]
        if len(piece) < sr // 2:
            continue
        res = run(piece, language, prompt)
        if language is None and res.get("language") not in (None, "zh"):
            # The Chinese prompt turns foreign speech into a rough Chinese
            # translation (Test A); read the piece again as it was spoken.
            res = run(piece, res["language"], None)
        tag = {"lang": res.get("language")} if language is None else {}
        for s in res.get("segments", []):
            if asr_kept(s):
                words += [{"word": w["word"], "start": a + w["start"], "end": a + w["end"], **tag}
                          for w in s.get("words", [])]
    return stats.sentences(words)


def beats_of(y: np.ndarray, sr: int, engine: str = "auto") -> tuple[list[float], list[float], str]:
    """beat_this when its checkpoint is cached (auto) or forced; else librosa (no downbeats).

    auto never triggers the 81 MB checkpoint download: a slow server would hang
    the run. Fetch it once with `--beats-engine beat_this` or by hand into
    torch.hub.get_dir()/checkpoints/beat_this-final0.ckpt.
    """
    if engine in ("auto", "beat_this"):
        try:
            import torch

            ck = Path(torch.hub.get_dir()) / "checkpoints" / "beat_this-final0.ckpt"
            if engine == "beat_this" or ck.exists():
                from beat_this.inference import Audio2Beats

                with contextlib.redirect_stdout(sys.stderr):
                    model = Audio2Beats(checkpoint_path="final0", device="cpu", dbn=False)
                    beats, downbeats = model(y.astype(np.float64), sr)
                return [rnd(b) for b in beats], [rnd(b) for b in downbeats], "beat_this"
            log("beat_this checkpoint not cached: using librosa (no downbeats)")
        except Exception as e:  # noqa: BLE001 - a broken checkpoint falls back in auto mode
            if engine == "beat_this":
                raise
            log(f"beat_this failed ({type(e).__name__}: {str(e)[:120]}): using librosa")
    import librosa

    _, times = librosa.beat.beat_track(y=y, sr=sr, units="time")
    return [rnd(b) for b in times], [], "librosa"


def tempo_check(y: np.ndarray, sr: int, bpm: float | None) -> dict:
    import librosa

    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    t = float(np.atleast_1d(tempo)[0])
    ok = None
    if bpm and t:
        ok = any(abs(t * k - bpm) / bpm < 0.04 for k in (0.5, 1, 2))
    return {"librosa_bpm": rnd(t, 1), "agrees": ok}


def onsets(y: np.ndarray, sr: int, beats: list[float], total: float) -> dict:
    import librosa

    env = librosa.onset.onset_strength(y=y, sr=sr)
    times = librosa.onset.onset_detect(onset_envelope=env, sr=sr, units="time")
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, units="frames")
    strong = []
    if len(frames):
        cut = np.percentile(env[frames], 95)
        b = np.asarray(beats) if beats else np.asarray([-1e9])
        for f, t in zip(frames, times):
            if env[f] >= cut and np.min(np.abs(b - t)) > 0.07:
                strong.append(rnd(t))
    return {"n": int(len(times)), "per_min": rnd(len(times) / max(total / 60, 1e-9), 1),
            "strong_off_beat": strong[:300]}


def envelope_db(y: np.ndarray, sr: int, hop_s: float = HOP_S) -> np.ndarray:
    hop = int(sr * hop_s)
    n = len(y) // hop
    if n == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(y[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms)


def music_changes(db: np.ndarray, beats: list[float]) -> dict:
    sil = [[rnd(a * HOP_S), rnd((b + 1) * HOP_S)] for a, b in stats_runs(db < -50) if (b - a + 1) * HOP_S >= 0.5]
    gaps = [[rnd(a), rnd(b)] for a, b in zip(beats, beats[1:]) if b - a > 2.5]
    changes = []
    if len(beats) > 32:
        ib = np.diff(beats)
        loc = np.array([np.median(ib[max(0, i - 8):i + 8]) for i in range(len(ib))])
        for i in range(16, len(loc) - 16, 4):
            before, after = np.median(loc[i - 16:i]), np.median(loc[i:i + 16])
            if abs(after - before) / before > 0.06:
                if not changes or beats[i] - changes[-1]["t"] > 8:
                    changes.append({"t": rnd(beats[i]), "bpm_before": rnd(60 / before, 1), "bpm_after": rnd(60 / after, 1)})
    return {"silences": sil, "beat_gaps": gaps, "tempo_changes": changes}


def stats_runs(mask) -> list[tuple[int, int]]:
    m = np.concatenate([[0], np.asarray(mask, np.int8), [0]])
    d = np.diff(m)
    return [(int(a), int(b) - 1) for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1))]


def duck_estimate(y: np.ndarray, sr: int, speech) -> dict | None:
    import librosa

    if not speech:
        return None
    hop = int(sr * HOP_S)
    S = np.abs(librosa.stft(y, n_fft=4096, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    band = 10 * np.log10(S[(freqs >= 30) & (freqs <= 90)].sum(axis=0) + 1e-12)
    t = np.arange(band.size) * HOP_S
    diffs = []
    for a, b in speech:
        if b - a < 1.0:
            continue
        inside = band[(t >= a + 0.3) & (t <= b - 0.3)]
        around = band[((t >= a - 8) & (t < a - 0.5)) | ((t > b + 0.5) & (t <= b + 8))]
        around = around[np.isfinite(around)]
        if inside.size > 5 and around.size > 10 and np.median(around) > -60:
            diffs.append(float(np.median(around) - np.median(inside)))
    if not diffs:
        return {"db": None, "n": 0, "note": "no music around speech to compare"}
    return {"db": rnd(float(np.median(diffs)), 1), "n": len(diffs),
            "note": "estimate from the 30-90 Hz band; a mix with no bass under speech reads as deep ducking"}


def music_mode(path: Path, out: Path, engine: str) -> int:
    y = decode(path, 22050)
    beats, downbeats, eng = beats_of(y, 22050, engine)
    res = {"source": path.name, "duration_s": rnd(len(y) / 22050), "bpm": stats.bpm_from_beats(beats), "engine": eng,
           "beats": beats, "downbeats": downbeats, "tempo_check": tempo_check(y, 22050, stats.bpm_from_beats(beats))}
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{path.name}: {res['bpm']} BPM, {len(beats)} beats -> {out.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path, nargs="?")
    ap.add_argument("--music", type=Path, help="beat-grid this music file instead")
    ap.add_argument("--out", type=Path, help="with --music: output json")
    ap.add_argument("--no-asr", action="store_true", help="skip mlx-whisper")
    ap.add_argument("--prompt", default=ASR_PROMPT, help="whisper initial prompt (names, places)")
    ap.add_argument("--language", default="zh", help="whisper language, or auto to detect it per piece")
    ap.add_argument("--sentences", action="store_true",
                    help="transcribe in pieces with word timestamps and regroup into sentences (apply)")
    ap.add_argument("--beats-engine", choices=["auto", "beat_this", "librosa"], default="auto")
    args = ap.parse_args(argv)
    if args.music:
        if not args.out:
            ap.error("--music needs --out")
        return music_mode(args.music.expanduser(), args.out.expanduser(), args.beats_engine)
    if not args.dir:
        ap.error("give DIR or --music FILE")
    d = args.dir.expanduser().resolve()
    prep = json.loads((d / "prep.json").read_text(encoding="utf-8"))
    if prep.get("kind") == "photo":  # its preview.jpg is the sheet; aggregate.py footage indexes it as it is
        log(f"{d.name}: a photo, nothing to measure")
        return 0
    audio = (prep.get("proxy") or {}).get("audio")
    if not audio:
        res = {"schema": "measure_audio/1", "skipped": "no audio stream"}
        (d / OUT).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        log("no audio stream")
        return 0
    music_wav, asr_wav = d / audio["music"], d / audio["asr"]
    y = decode(music_wav, 22050)
    total = len(y) / 22050
    res: dict = {"schema": "measure_audio/1", "duration_s": rnd(total), "loudness": loudness(music_wav)}

    captions, sources = [], []
    mv = d / "measure_video.json"
    if mv.exists():
        captions = (json.loads(mv.read_text(encoding="utf-8")).get("text") or {}).get("transcript") or []
    plat = platform_subs(d)
    segs = []
    if not args.no_asr:
        log(f"whisper ({ASR_REPO})")
        language = None if args.language == "auto" else args.language
        segs = (whisper_sentences if args.sentences else whisper)(asr_wav, args.prompt, language)
    spans = []
    for name, rows in (("captions", captions), ("platform", plat), ("whisper", segs)):
        if rows:
            sources.append(name)
            spans += [(r["t_in"], r["t_out"]) for r in rows]
    speech = merge(spans)
    res["speech"] = speech
    res["speech_sources"] = sources
    res["speech_share"] = rnd(sum(b - a for a, b in speech) / max(total, 1e-9))
    best = captions or plat or segs
    res["transcript"] = {"source": sources[0] if sources else None, "segments": best,
                         "whisper": segs if best is not segs else None}
    if args.sentences or args.language != "zh":
        res["transcript"]["asr"] = {"language": args.language, "sentences": args.sentences}

    log("beats")
    beats, downbeats, eng = beats_of(y, 22050, args.beats_engine)
    nonspeech = complement(speech, total)
    ns_total = sum(b - a for a, b in nonspeech)
    bpm_all = stats.bpm_from_beats(beats)
    res["beats"] = {"engine": eng, "bpm": stats.bpm_from_beats(beats, spans=nonspeech) or bpm_all, "bpm_all": bpm_all,
                    "beats": beats, "downbeats": downbeats, "nonspeech_spans": nonspeech,
                    "nonspeech_s": rnd(ns_total, 1),
                    "confidence": "ok" if ns_total >= 30 or ns_total >= 0.25 * total else "uncertain"}
    res["tempo_check"] = tempo_check(y, 22050, res["beats"]["bpm"])
    res["onsets"] = onsets(y, 22050, beats, total)
    res["music"] = music_changes(envelope_db(y, 22050), beats)
    res["duck"] = duck_estimate(decode(music_wav, 44100), 44100, speech)
    (d / OUT).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"done: {res['beats']['bpm']} BPM ({res['beats']['confidence']}), speech {res['speech_share']:.0%}, "
        f"{res['loudness']['integrated_lufs']} LUFS -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

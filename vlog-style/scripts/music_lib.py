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
#   "transformers",
#   "demucs",
# ]
# ///
# -*- coding: utf-8 -*-
"""Shared music library for choosing a vlog's soundtrack.

  python3 -I -B music_lib.py add FILE [--title T] [--artist A] [--license STATUS] [--license-name N]
                                [--license-url URL] [--source-url URL] [--attribution TEXT]
  python3 -I -B music_lib.py fetch URL [--title T] [--artist A] [--referer URL] [--license ...]
  python3 -I -B music_lib.py list [--project NAME] [--json]
  uv run --python 3.13 --script music_lib.py scan [--project NAME] [--force] [--no-models] [ID ...]

Layout (every path stored relative to root):
  music/<id>/<id>.<ext>   the audio, copied in; the user's original is never moved or changed
  music/<id>/track.json   music_track/1: title, artist, sha1, license, measured
  music/<id>/embed.json   CLAP vectors (whole track and per section), for ranking
  projects/<p>/music/     the user's own tracks for one project; `scan --project` measures them into
                          projects/<p>/index/music/<id>.json and <id>.embed.json

License status is a label, never a filter (user, 2026-11-14): cleared, attribution, needs_license,
unknown. `fetch` downloads audio only, into a new empty cache/music-dl/<stamp>/, never logs in, and
refuses DRM-protected or encrypted streams, previews (SoundCloud Go+) and Bandcamp's streaming copy of
a track that isn't a free download. The download folder goes to the Trash once the file is in the
library.

add, fetch and list need only the standard library and ffprobe (plus yt-dlp for fetch). scan needs
uv: loudness, beats and tempo come from measure_audio.py; it adds per-second energy, sections, intro,
climax, ending, key, and with models on, how much is sung (demucs htdemucs) and CLAP tags and vectors
(laion/clap-htsat-unfused). A track whose file sha1 and measure version are unchanged is skipped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402

SCHEMA = "music_track/1"
EMBED_SCHEMA = "music_embed/1"
MEASURE_VERSION = 1
STATUSES = ("cleared", "attribution", "needs_license", "unknown")
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg", ".opus", ".wma", ".webm", ".mka"}
RESOLVE_EXT = {".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff"}  # anything else is decoded to WAV on the way in
MIN_S, MAX_S = 10.0, 1800.0
CLAP_MODEL = "laion/clap-htsat-unfused"  # larger_clap_music's text side collapses: learnings.md 2026-11-14
DEMUCS_MODEL = "htdemucs"
FLOOR_DB = -60.0  # below this a second or a hop counts as silence

# Zero-shot vocabulary for CLAP. English goes to the model; STYLE.md and the candidate table show the Chinese.
TAGS = {
    "genre": [("cinematic orchestral", "电影感管弦"), ("ambient", "氛围"), ("acoustic folk", "原声民谣"),
              ("indie pop", "独立流行"), ("japanese pop", "日系流行"), ("lo-fi hip hop", "lo-fi"),
              ("chill electronic", "电子 chill"), ("post-rock", "后摇"), ("jazz", "爵士"),
              ("solo piano", "钢琴独奏"), ("rock", "摇滚"), ("upbeat EDM", "EDM")],
    "mood": [("calm", "平静"), ("melancholic", "忧郁"), ("warm", "温暖"), ("uplifting", "昂扬"),
             ("nostalgic", "怀旧"), ("epic", "壮阔"), ("playful", "轻快"), ("mysterious", "神秘"),
             ("romantic", "浪漫"), ("lonely", "孤独")],
    "instrument": [("piano", "钢琴"), ("acoustic guitar", "木吉他"), ("strings", "弦乐"), ("synthesizer", "合成器"),
                   ("drums", "鼓"), ("female vocals", "女声"), ("male vocals", "男声"), ("bells", "钟琴"),
                   ("flute", "长笛"), ("ukulele", "尤克里里")],
    "energy": [("very quiet and slow", "很静很慢"), ("slow and gentle", "慢而柔"), ("medium tempo", "中速"),
               ("upbeat and energetic", "明快有劲"), ("intense and powerful", "强烈")],
}
PROMPT = {"genre": "{} music", "mood": "{} music", "instrument": "music with {}", "energy": "{} music"}

# Krumhansl-Kessler key profiles, index 0 = tonic.
MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def log(msg: str) -> None:
    print(f"[music_lib] {msg}", file=sys.stderr, flush=True)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- license


def license_status(text: str | None) -> str:
    """Map a source's license text (yt-dlp `license`, a page footer, a CC URL) to one of STATUSES."""
    t = " ".join((text or "").lower().split())
    if not t:
        return "unknown"
    if re.search(r"\bcc0\b|public ?domain|publicdomain|no[- ]rights[- ]reserved|pixabay|mixkit free", t):
        return "cleared"
    if re.search(r"all[- ]rights[- ]reserved|standard youtube licen|restricted licen|fma[- ]limited|download only"
                 r"|non-?commercial|no-?deriv", t):
        return "needs_license"
    if re.search(r"creative ?commons|creativecommons\.org/licenses|\bcc[- ]by|\bby-(?:sa|nc|nd)", t):
        # the bare NC / ND abbreviations only mean something next to a CC name
        return "needs_license" if re.search(r"\bnc\b|-nc\b|\bnd\b|-nd\b", t) else "attribution"
    if re.search(r"\battribution\b", t):
        return "attribution"
    return "unknown"


def default_attribution(title: str, artist: str | None, lic: dict) -> str:
    """A credit line to start from. Sources with a fixed wording get it; check others against the source page."""
    src = (lic.get("source_url") or "").lower()
    if "incompetech.com" in src or (artist or "").lower() == "kevin macleod":
        return (f"{title} Kevin MacLeod (incompetech.com)\nLicensed under Creative Commons: By Attribution 4.0\n"
                "https://creativecommons.org/licenses/by/4.0/")
    lines = [f"“{title}”" + (f" by {artist}" if artist else "")]
    named = " ".join(x for x in (lic.get("name"), lic.get("url")) if x)
    if named:
        lines.append(named)
    if lic.get("source_url"):
        lines.append(lic["source_url"])
    return "\n".join(lines)


def make_license(status: str | None = None, name: str | None = None, url: str | None = None,
                 attribution: str | None = None, source_url: str | None = None, fetched: str | None = None,
                 note: str | None = None) -> dict:
    if status is not None and status not in STATUSES:
        raise SystemExit(f"license status {status!r} is not one of {', '.join(STATUSES)}")
    lic = {"status": status or license_status(" ".join(x for x in (name, url) if x)), "name": name, "url": url,
           "attribution": attribution, "source_url": source_url, "fetched": fetched}
    if note:
        lic["note"] = note
    return lic


# ---------------------------------------------------------------- library files


def slug(text: str, limit: int = 40) -> str:
    s = unicodedata.normalize("NFKC", text or "").strip().lower()
    s = re.sub(r"[^\w.-]+", "-", s)
    s = re.sub(r"[-.]{2,}", "-", s).strip("-._")[:limit].rstrip("-._")
    return s or "track"


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_audio(path: Path) -> dict:
    """Duration, and whether there is real video (cover art does not count). Refuses non-audio and odd lengths."""
    if not shutil.which("ffprobe"):
        raise SystemExit("ffprobe not on PATH")
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type:stream_disposition=attached_pic", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{path.name}: ffprobe could not read it ({r.stderr.strip()[-200:]})")
    d = json.loads(r.stdout or "{}")
    streams = d.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams
             if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    dur = float((d.get("format") or {}).get("duration") or 0)
    if not audio:
        raise SystemExit(f"{path.name}: no audio stream")
    if not MIN_S <= dur <= MAX_S:
        raise SystemExit(f"{path.name}: {dur:.1f} s is outside {MIN_S:.0f}-{MAX_S:.0f} s, not a usable soundtrack")
    return {"duration_s": round(dur, 3), "video": bool(video)}


def library(P: vpaths.Paths) -> list[dict]:
    root = P.dir("music")
    out = []
    for tj in sorted(root.glob("*/track.json")) if root.is_dir() else []:
        try:
            out.append(json.loads(tj.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            log(f"skipping {P.rel(tj)}: {e}")
    return out


def project_tracks(P: vpaths.Paths, project: str) -> list[dict]:
    idx = P.project(project) / "index" / "music"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(idx.glob("*.json"))
            if not p.name.endswith(".embed.json")] if idx.is_dir() else []


def add(P: vpaths.Paths, src: Path, title: str | None = None, artist: str | None = None, lic: dict | None = None,
        today: str | None = None) -> tuple[dict, bool]:
    """Copy one audio file into the library. Returns (track, added); the same file twice gives the first track."""
    src = Path(src).expanduser()
    if not src.is_file():
        raise SystemExit(f"{src}: not a file")
    info = probe_audio(src)
    sha = sha1_of(src)
    for t in library(P):
        if t.get("sha1") == sha:
            return t, False
    title = (title or src.stem).strip()
    tid = vpaths.check_name(f"{slug(title)}-{sha[:6]}")
    d = P.music(tid)
    d.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    if info["video"] or ext not in RESOLVE_EXT:
        dst = d / f"{tid}.wav"
        r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn",
                            "-c:a", "pcm_s24le", "-ar", "48000", str(dst)], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"{src.name}: ffmpeg could not decode it: {r.stderr.strip()[-300:]}")
    else:
        dst = d / f"{tid}{ext}"
        shutil.copy2(src, dst)
    lic = dict(lic or make_license())
    if lic["status"] == "attribution" and not lic.get("attribution"):
        lic["attribution"] = default_attribution(title, artist, lic)
        lic["attribution_auto"] = True  # generated: check it against the wording the source asks for
    track = {"schema": SCHEMA, "id": tid, "title": title, "artist": artist, "file": P.rel(dst), "sha1": sha,
             "duration_s": info["duration_s"], "added": today or dt.date.today().isoformat(), "license": lic,
             "measured": None}
    write_json(d / "track.json", track)
    return track, True


def refuse_reason(info: dict) -> str | None:
    """Why a yt-dlp download must not enter the library: DRM, encryption, a preview, Bandcamp's paid stream."""
    fmt = " ".join(str(info.get(k) or "") for k in ("format_id", "format", "format_note", "protocol")).lower()
    downloads = [f for f in info.get("requested_downloads") or [] if isinstance(f, dict)]
    if info.get("has_drm") or any(f.get("has_drm") for f in downloads):
        return "DRM-protected"
    if "hls-aes" in fmt:
        return "encrypted stream"
    if "preview" in fmt:
        return "only a preview (SoundCloud Go+ and the like)"
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    if extractor.startswith("bandcamp") and str(info.get("format_id") or "").startswith("mp3-128"):
        return "Bandcamp's streaming copy; only tracks offered as a free download are taken"
    return None


def trash_dir(path: Path) -> None:
    if not path.exists():
        return
    tool = shutil.which("trash")
    if tool:
        subprocess.run([tool, str(path)], capture_output=True)
    else:
        log(f"`trash` not found; {path} is left for you to remove")


def fetch(P: vpaths.Paths, url: str, title: str | None = None, artist: str | None = None,
          referer: str | None = None, status: str | None = None, license_name: str | None = None,
          license_url: str | None = None, attribution: str | None = None,
          today: str | None = None) -> tuple[dict, bool]:
    """Download one track's audio with yt-dlp (no login) and add it. The download folder always goes to the Trash."""
    tool = shutil.which("yt-dlp")
    if not tool:
        raise SystemExit("yt-dlp not on PATH")
    today = today or dt.date.today().isoformat()
    dl = P.dir("cache") / "music-dl" / dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dl.mkdir(parents=True)  # a new empty folder for every download
    try:
        cmd = [tool, "--no-playlist", "--no-progress", "--max-filesize", "500M", "-f", "bestaudio/best", "-x",
               "--write-info-json", "-o", str(dl / "a.%(ext)s")]
        if referer:
            cmd += ["--add-headers", f"Referer:{referer}"]
        r = subprocess.run([*cmd, "--", url], capture_output=True, text=True, timeout=1800)
        if r.returncode:
            raise SystemExit(f"yt-dlp failed (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-600:]}")
        infos = sorted(dl.glob("*.info.json"))
        info = json.loads(infos[0].read_text(encoding="utf-8")) if infos else {}
        why = refuse_reason(info)
        if why:
            raise SystemExit(f"{url}: {why}; not added")
        files = [p for p in dl.iterdir() if p.is_file() and not p.name.endswith((".json", ".part", ".ytdl"))]
        if len(files) != 1:
            raise SystemExit(f"{url}: expected one audio file, got {[p.name for p in files]}")
        got = probe_audio(files[0])
        want = info.get("duration")
        if want and got["duration_s"] < 0.9 * float(want) - 1:
            raise SystemExit(f"{url}: got {got['duration_s']:.0f} s of a {float(want):.0f} s track (a preview or cut "
                             "short); not added")
        lic = make_license(status=status, name=license_name or info.get("license"), url=license_url,
                           attribution=attribution, source_url=info.get("webpage_url") or url, fetched=today)
        name = title or info.get("track") or info.get("title")
        who = artist or info.get("artist") or info.get("creator") or info.get("uploader")
        return add(P, files[0], name, who, lic, today)
    finally:
        trash_dir(dl)


# ---------------------------------------------------------------- pure analysis (no numpy, tested with stdlib)


def pct(xs: list[float], q: float) -> float | None:
    s = sorted(xs)
    if not s:
        return None
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def moving_mean(xs: list[float], n: int) -> list[float]:
    if n <= 1:
        return list(xs)
    half, out = n // 2, []
    for i in range(len(xs)):
        lo, hi = max(0, i - half), min(len(xs), i + half + 1)
        out.append(sum(xs[lo:hi]) / (hi - lo))
    return out


def power_mean(xs: list[float], n: int) -> list[float]:
    """Moving energetic mean of a dB curve. A drum track is as loud as its hits: a plain mean of dB values
    would let the gaps between hits drag it down by 20 dB or more."""
    return [10 * math.log10(max(v, 1e-30)) for v in moving_mean([10 ** (x / 10) for x in xs], n)]


def body_level(curve: list[float]) -> float | None:
    """The level the song spends most of its loud time at: 75th percentile of the non-silent points."""
    return pct([e for e in curve if e > FLOOR_DB], 0.75)


def intro_of(energy: list[float], hop: float = 1.0) -> float | None:
    """When the song gets going: first time the 3-s energetic mean comes within 6 dB of the body level."""
    sm = power_mean(energy, max(1, round(3 / hop)))
    level = body_level(sm)
    if level is None:
        return None
    for i, e in enumerate(sm):
        if e >= level - 6:
            return round(i * hop, 2)
    return None


def count_peaks(seg: list[float], hop: float, prom: float = 3.0) -> int:
    """Hits in a stretch of envelope: local maxima standing at least `prom` dB over the dip before them."""
    w, back = max(1, round(0.15 / hop)), max(1, round(0.3 / hop))
    n, last = 0, -10 ** 9
    for i, v in enumerate(seg):
        if v <= FLOOR_DB or v < max(seg[max(0, i - w):i + w + 1]):
            continue
        if v - min(seg[max(0, i - back):i + 1]) < prom or i - last < w:
            continue
        n, last = n + 1, i
    return n


def ending_of(env: list[float], hop: float = 0.05) -> dict | None:
    """How the song ends. hard: stops at full level; fade: music goes on while it gets quieter, or a long
    soft close (6 s or more); ring: a last chord or hit dies away on its own."""
    active = [i for i, e in enumerate(env) if e > FLOOR_DB]
    if not active:
        return None
    end = active[-1] + 1
    sm = power_mean(env, max(1, round(1 / hop)))
    level = body_level(sm[:end])
    loud = [i for i in range(end) if sm[i] >= level - 6]
    t_loud = loud[-1] + 1 if loud else 0
    tail = (end - t_loud) * hop
    rate = count_peaks(env[t_loud:end], hop) / tail if tail > 0 else 0.0
    kind = "hard" if tail <= 1.5 else "fade" if rate >= 1.0 or tail >= 6.0 else "ring"
    return {"type": kind, "tail_s": round(tail, 2), "end_s": round(end * hop, 2), "hits_per_s": round(rate, 2)}


def climax_of(sections: list[dict], min_len: float = 4.0) -> float | None:
    """Start of the loudest section; of sections within 0.5 dB of it, the last (the final chorus)."""
    ok = [s for s in sections if s["t1"] - s["t0"] >= min_len and s.get("energy_db") is not None]
    if not ok:
        return None
    top = max(s["energy_db"] for s in ok)
    return [s for s in ok if s["energy_db"] >= top - 0.5][-1]["t0"]


def energetic_mean(xs: list[float]) -> float | None:
    return round(10 * math.log10(sum(10 ** (x / 10) for x in xs) / len(xs)), 1) if xs else None


def sections_from_bounds(bounds: list[float], energy: list[float], duration: float) -> list[dict]:
    edges = sorted({0.0, *[round(b, 2) for b in bounds if 0 < b < duration]}) + [round(duration, 2)]
    return [{"t0": a, "t1": b, "energy_db": energetic_mean(energy[int(a):max(int(a) + 1, int(b))])}
            for a, b in zip(edges, edges[1:])]


def _corr(a: list[float], b: list[float]) -> float:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def key_of(chroma: list[float]) -> dict | None:
    """Krumhansl key estimate from the mean chroma. margin is how far ahead of the runner-up it is."""
    if not chroma or len(chroma) != 12 or sum(chroma) <= 0:
        return None
    scores = sorted(((_corr(chroma, [prof[(p - k) % 12] for p in range(12)]), NOTES[k], mode)
                     for mode, prof in (("major", MAJOR), ("minor", MINOR)) for k in range(12)), reverse=True)
    (best, tonic, mode), second = scores[0], scores[1][0]
    return {"tonic": tonic, "mode": mode, "strength": round(best, 3), "margin": round(best - second, 3)}


def runs(flags: list[bool], hop: float, gap: float = 1.0, min_len: float = 2.0) -> list[list[float]]:
    spans: list[list[float]] = []
    for i, f in enumerate(flags):
        if not f:
            continue
        a, b = i * hop, (i + 1) * hop
        if spans and a - spans[-1][1] <= gap:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    return [[round(a, 2), round(b, 2)] for a, b in spans if b - a >= min_len]


def vocal_of(voc: list[float], acc: list[float], hop: float = 1.0, exclude=(), margin: float = 6.0,
             floor: float = -45.0) -> dict:
    """How much of the music is sung, from demucs's per-second vocals and accompaniment levels.

    A second counts as sung when the vocals are above `floor` and within `margin` dB of the accompaniment.
    demucs also puts talking in the vocals stem, so a reference passes its speech spans as `exclude`
    (learnings.md 2026-11-14).
    """
    flags, active = [], 0
    for i in range(min(len(voc), len(acc))):
        t0, t1 = i * hop, (i + 1) * hop
        if any(a < t1 and b > t0 for a, b in exclude) or max(voc[i], acc[i]) <= -50:
            flags.append(False)
            continue
        active += 1
        flags.append(voc[i] > floor and voc[i] > acc[i] - margin)
    share = sum(flags) / active if active else None
    verdict = None if share is None else "vocal" if share >= 0.3 else "instrumental" if share <= 0.05 else "mixed"
    return {"share": None if share is None else round(share, 3), "verdict": verdict, "spans": runs(flags, hop),
            "active_s": round(active * hop, 1)}


# ---------------------------------------------------------------- measuring (numpy, librosa, models; via uv)

_MODELS: dict = {}


def _device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def sections_of(y, sr: int, beats: list[float], downbeats: list[float], energy: list[float],
                duration: float) -> list[dict]:
    """Structural sections: agglomerative clustering of beat-synced chroma + MFCC, snapped to downbeats."""
    import librosa
    import numpy as np

    hop = 512
    feats = np.vstack([librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop),
                       librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=hop)])
    n = feats.shape[1]
    grid = (librosa.time_to_frames(beats, sr=sr, hop_length=hop) if len(beats) >= 16
            else np.arange(0, n, max(1, int(0.5 * sr / hop))))
    edges = librosa.util.fix_frames(np.clip(grid, 0, n), x_min=0, x_max=n)
    synced = librosa.util.sync(feats, edges, aggregate=np.median, pad=False)
    synced = (synced - synced.mean(axis=1, keepdims=True)) / (synced.std(axis=1, keepdims=True) + 1e-8)
    k = int(min(np.clip(round(duration / 25), 2, 12), synced.shape[1]))
    starts = librosa.segment.agglomerative(synced, k)
    times = librosa.frames_to_time(edges[starts], sr=sr, hop_length=hop)
    bounds: list[float] = []
    for t in sorted(float(x) for x in times if x > 0):
        near = [d for d in downbeats if abs(d - t) <= 1.5]
        t = min(near, key=lambda d: abs(d - t)) if near else t
        if t - (bounds[-1] if bounds else 0.0) >= 4.0 and duration - t >= 4.0:
            bounds.append(t)
    return sections_from_bounds(bounds, energy, duration)


def chroma_of(y, sr: int) -> list[float]:
    import librosa
    import numpy as np

    c = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=2048)
    rms = librosa.feature.rms(y=y, hop_length=2048)[0][: c.shape[1]]
    keep = rms > 10 ** (FLOOR_DB / 20)
    return [round(float(v), 4) for v in (c[:, keep] if keep.any() else c).mean(axis=1)]


def demucs_levels(path: Path) -> tuple[list[float], list[float]]:
    """Per-second dB of the vocals stem and of the loudest other stem."""
    import numpy as np
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    import measure_audio as ma

    if "demucs" not in _MODELS:
        model = get_model(DEMUCS_MODEL).eval()
        dev = _device()
        try:
            model.to(dev)
        except Exception:  # noqa: BLE001 - mps can refuse a model; cpu always works
            dev = "cpu"
        _MODELS["demucs"] = (model, dev)
    model, dev = _MODELS["demucs"]
    sr = model.samplerate
    wav = np.ascontiguousarray(ma.decode(path, sr, 2).T)
    vi = model.sources.index("vocals")
    voc: list[float] = []
    acc: list[float] = []
    chunk = 60 * sr
    for a in range(0, wav.shape[1], chunk):
        seg = torch.from_numpy(wav[:, a:a + chunk])
        ref = seg.mean(0)
        mu, sd = ref.mean(), ref.std() + 1e-8
        with torch.no_grad():
            out = apply_model(model, ((seg - mu) / sd)[None], device=dev, split=True, overlap=0.25, progress=False)[0]
        mono = (out * sd).cpu().numpy().mean(axis=1)
        secs = mono.shape[1] // sr
        if secs == 0:
            continue
        db = 20 * np.log10(np.sqrt((mono[:, :secs * sr].reshape(len(model.sources), secs, sr) ** 2).mean(2)) + 1e-9)
        voc += [round(float(v), 1) for v in db[vi]]
        acc += [round(float(v), 1) for v in np.delete(db, vi, axis=0).max(axis=0)]
    return voc, acc


def _clap():
    if "clap" not in _MODELS:
        from transformers import ClapModel, ClapProcessor

        model = ClapModel.from_pretrained(CLAP_MODEL).eval()
        dev = _device()
        try:
            model.to(dev)
        except Exception:  # noqa: BLE001
            dev = "cpu"
        _MODELS["clap"] = (model, ClapProcessor.from_pretrained(CLAP_MODEL), dev)
    return _MODELS["clap"]


def _feats(x, kind: str):
    """transformers 5 returns an output object from get_*_features (take pooler_output); 4 returns the tensor."""
    import torch

    if torch.is_tensor(x):
        return x
    for k in (f"{kind}_embeds", "pooler_output"):
        v = getattr(x, k, None)
        if v is not None:
            return v
    raise TypeError(f"no {kind} embedding in {type(x).__name__}")


def clap_text(texts: list[str]):
    """Unit CLAP text vectors (numpy, one row per text). Also used by ranking for brief.music_query."""
    import torch

    model, proc, dev = _clap()
    tin = {k: v.to(dev) for k, v in proc(text=texts, return_tensors="pt", padding=True).items()}
    with torch.no_grad():
        t = _feats(model.get_text_features(**tin), "text")
    return torch.nn.functional.normalize(t, dim=-1).cpu().numpy()


def clap_windows(y, sr: int = 48000, window_s: float = 10.0):
    """Unit CLAP audio vectors for each non-silent window, with the windows' (t0, t1)."""
    import numpy as np
    import torch

    model, proc, dev = _clap()
    w = int(window_s * sr)
    clips, spans = [], []
    for a in range(0, max(1, len(y) - w + 1), w):
        c = y[a:a + w]
        if 20 * np.log10(np.sqrt(np.mean(c ** 2)) + 1e-12) >= -50:
            clips.append(c)
            spans.append((a / sr, min(len(y), a + w) / sr))
    rows = []
    for i in range(0, len(clips), 16):
        ain = {k: v.to(dev) for k, v in proc(audio=clips[i:i + 16], sampling_rate=sr, return_tensors="pt").items()}
        with torch.no_grad():
            e = _feats(model.get_audio_features(**ain), "audio")
        rows.append(torch.nn.functional.normalize(e, dim=-1).cpu().numpy())
    return (np.concatenate(rows) if rows else np.zeros((0, 512), np.float32)), spans


def unit(v):
    import numpy as np

    n = float(np.linalg.norm(v))
    return v / n if n else v


def clap_of(path: Path, sections: list[dict]) -> tuple[list[dict], dict | None]:
    import numpy as np

    import measure_audio as ma

    emb, spans = clap_windows(ma.decode(path, 48000))
    if not len(emb):
        return [], None
    track = unit(emb.mean(axis=0))
    per_section = []
    for s in sections:
        idx = [j for j, (a, b) in enumerate(spans) if s["t0"] <= (a + b) / 2 < s["t1"]]
        per_section.append([round(float(x), 5) for x in unit(emb[idx].mean(axis=0))] if idx else None)
    keys = [(cat, en, zh) for cat, items in TAGS.items() for en, zh in items]
    sims = clap_text([PROMPT[c].format(en) for c, en, _ in keys]) @ track
    tags = []
    for cat in TAGS:
        top = sorted(((float(sims[j]), keys[j]) for j in range(len(keys)) if keys[j][0] == cat), reverse=True)[:3]
        tags += [{"cat": cat, "tag": en, "zh": zh, "score": round(s, 3)} for s, (_, en, zh) in top]
    embed = {"schema": EMBED_SCHEMA, "model": CLAP_MODEL, "dim": int(track.shape[0]), "window_s": 10,
             "track": [round(float(x), 5) for x in track], "sections": per_section}
    return tags, embed


def measure_track(path: Path, models: bool = True) -> tuple[dict, dict | None]:
    import measure_audio as ma
    import stats

    sr = 22050
    y = ma.decode(path, sr)
    duration = len(y) / sr
    beats, downbeats, engine = ma.beats_of(y, sr)
    bpm = stats.bpm_from_beats(beats)
    energy = [round(max(float(e), -90.0), 1) for e in ma.envelope_db(y, sr, 1.0)]
    env = [max(float(e), -90.0) for e in ma.envelope_db(y, sr, ma.HOP_S)]
    sections = sections_of(y, sr, beats, downbeats, energy, duration)
    m = {"version": MEASURE_VERSION, "models": models, "duration_s": round(duration, 3),
         "loudness": ma.loudness(path),
         "beats": {"engine": engine, "bpm": bpm, "beats": beats, "downbeats": downbeats,
                   "tempo_check": ma.tempo_check(y, sr, bpm)},
         "energy_db": energy, "sections": sections, "intro_s": intro_of(energy), "climax_s": climax_of(sections),
         "ending": ending_of(env, ma.HOP_S), "key": key_of(chroma_of(y, sr)), "vocal": None, "tags": []}
    embed = None
    if models:
        m["vocal"] = vocal_of(*demucs_levels(path))
        m["tags"], embed = clap_of(path, sections)
    return m, embed


def scan(P: vpaths.Paths, project: str | None = None, ids=(), force: bool = False, models: bool = True,
         today: str | None = None) -> list[dict]:
    """Measure library tracks (or a project's own tracks) that are new or changed. Returns the records written."""
    today = today or dt.date.today().isoformat()
    jobs = []  # (record, audio, json path, embed path)
    if project:
        pdir = P.project(project)
        idx = pdir / "index" / "music"
        files = sorted(p for p in (pdir / "music").rglob("*")
                       if p.is_file() and p.suffix.lower() in AUDIO_EXT) if (pdir / "music").is_dir() else []
        known = {t["file"]: t for t in project_tracks(P, project)}
        taken: set[str] = set()
        for f in files:
            rel = P.rel(f)
            rec = known.get(rel)
            if rec is None:
                tid = slug(f.stem)
                if tid in taken or (idx / f"{tid}.json").exists():
                    tid = f"{tid}-{sha1_of(f)[:6]}"
                rec = {"schema": SCHEMA, "id": tid, "title": f.stem, "artist": None, "file": rel, "sha1": None,
                       "duration_s": None, "added": today,
                       "license": make_license(note="项目自带的曲子，授权由用户处理"), "measured": None}
            taken.add(rec["id"])
            jobs.append((rec, f, idx / f"{rec['id']}.json", idx / f"{rec['id']}.embed.json"))
    else:
        want = set(ids)
        for t in library(P):
            if want and t["id"] not in want:
                continue
            audio = P.abs(t["file"])
            jobs.append((t, audio, audio.parent / "track.json", audio.parent / "embed.json"))
        missing = want - {t["id"] for t, *_ in jobs}
        if missing:
            raise SystemExit(f"not in the library: {', '.join(sorted(missing))}")
    done = []
    for rec, audio, jpath, epath in jobs:
        sha = sha1_of(audio)
        if project:
            rec["sha1"] = sha
        m = rec.get("measured") or {}
        if (not force and m.get("version") == MEASURE_VERSION and m.get("file_sha1") == sha
                and (m.get("models") or not models)):
            log(f"{rec['id']}: unchanged, skipped")
            continue
        log(f"{rec['id']}: measuring{'' if models else ' (no models)'}")
        measured, embed = measure_track(audio, models)
        measured.update(file_sha1=sha, date=today)
        rec["measured"] = measured
        rec["duration_s"] = measured["duration_s"]
        write_json(jpath, rec)
        if embed:
            write_json(epath, embed)
        done.append(rec)
        print(summary_line(rec))
    return done


# ---------------------------------------------------------------- listing and CLI


def summary_line(t: dict) -> str:
    m = t.get("measured") or {}
    bpm = (m.get("beats") or {}).get("bpm")
    vocal = (m.get("vocal") or {}).get("verdict") or "-"
    key = m.get("key") or {}
    ending = (m.get("ending") or {}).get("type") or "-"
    tags = " ".join(x["zh"] for x in m.get("tags") or [] if x["cat"] in ("genre", "mood"))
    secs = round(t["duration_s"]) if t.get("duration_s") else None
    return "\t".join([t["id"], t.get("title") or "", t.get("artist") or "-",
                      f"{secs // 60}:{secs % 60:02d}" if secs else "-", f"{bpm:.0f}" if bpm else "-", vocal,
                      f"{key.get('tonic')}{'m' if key.get('mode') == 'minor' else ''}" if key else "-", ending,
                      t["license"]["status"], tags or ("未测" if not m else "-")])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def license_args(p):
        p.add_argument("--license", choices=STATUSES, help="status; default: read from the license name or URL")
        p.add_argument("--license-name")
        p.add_argument("--license-url")
        p.add_argument("--attribution", help="credit text the source asks for")

    a = sub.add_parser("add", help="copy an audio file into the library")
    a.add_argument("file", type=Path)
    a.add_argument("--title")
    a.add_argument("--artist")
    a.add_argument("--source-url")
    license_args(a)
    f = sub.add_parser("fetch", help="download a track's audio (yt-dlp, no login) and add it")
    f.add_argument("url")
    f.add_argument("--title")
    f.add_argument("--artist")
    f.add_argument("--referer", help="send this page as Referer (ccMixter's download links want it)")
    license_args(f)
    lst = sub.add_parser("list", help="list the library (and a project's own tracks)")
    lst.add_argument("--project")
    lst.add_argument("--json", action="store_true")
    s = sub.add_parser("scan", help="measure new or changed tracks (needs uv)")
    s.add_argument("ids", nargs="*")
    s.add_argument("--project", help="measure projects/<p>/music/ instead of the library")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-models", action="store_true", help="skip demucs and CLAP (vocal, tags, embed.json)")
    args = ap.parse_args(argv)
    P = vpaths.load()

    if args.cmd == "add":
        lic = make_license(args.license, args.license_name, args.license_url, args.attribution, args.source_url)
        track, new = add(P, args.file, args.title, args.artist, lic)
        print(f"{'added' if new else 'already in the library'}: {track['id']} ({track['license']['status']})")
        return 0
    if args.cmd == "fetch":
        track, new = fetch(P, args.url, args.title, args.artist, args.referer, args.license, args.license_name,
                           args.license_url, args.attribution)
        print(f"{'added' if new else 'already in the library'}: {track['id']} ({track['license']['status']})")
        return 0
    if args.cmd == "list":
        rows = library(P) + (project_tracks(P, args.project) if args.project else [])
        if args.json:
            slim = [{k: v for k, v in t.items() if k != "measured"} | {"measured": bool(t.get("measured"))}
                    for t in rows]
            print(json.dumps(slim, ensure_ascii=False, indent=1))
        else:
            print("id\ttitle\tartist\tlen\tbpm\tvocal\tkey\tending\tlicense\ttags")
            for t in rows:
                print(summary_line(t))
        return 0
    scan(P, args.project, args.ids, args.force, not args.no_models)
    return 0


if __name__ == "__main__":
    sys.exit(main())

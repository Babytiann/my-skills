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
"""How a reference uses its music: where it plays, how many songs, sung or not, under the talking or not.

  uv run --python 3.13 --script measure_music.py DIR [--audio FILE]
  uv run --python 3.13 --script measure_music.py DIR --rebuild

--rebuild re-reads the levels, scores and vectors the last run saved and only redoes the analysis below (seconds,
no demucs), for when a threshold here changes.

DIR is a reference folder with measure_audio.json (cache/refs/<id> or a kept styles/<group>/refs/<id>);
labels.json is read for chapter cuts when it is there. The audio is DIR/music44k.wav, or --audio (any media
ffmpeg reads, e.g. a re-downloaded src/*.mp4) for a kept folder that has none.

demucs splits the sound into vocals and accompaniment in 60 s chunks. Every 10 s window then gets:
  music  CLAP on the accompaniment (talking removed): best music prompt minus best ambient prompt
  sing   CLAP on the vocals stem: best singing prompt minus best talking prompt (demucs puts talking in the
         vocals stem too, and lyric captions sit in measure_audio's speech spans, so neither tells singing alone)
  acc_db, voc_db  median per-second level of each stem; speech: share of the window in the speech spans
A window is music when `music` > 0.1 and the accompaniment is above -50 dB. Songs: runs of music windows,
split where the music of the 30 s before and after an edge differs (CLAP context similarity < 0.70) and a
measured change candidate (silence, beat gap, tempo change) is within 6 s; under 0.55 it splits without one.
Pieces under 20 s join their closer neighbour; a lone run under 20 s is a sting, counted apart.
body_s is where the music takes over: the first two music windows in a row within 8 dB of the film's usual music
level, so a quiet bed under the opening talk or a short opening sting does not count as the song coming in.
A piece shorter than 30 s whose median level is more than 8 dB under that usual level is a bed (a short, quiet
underlay): it is listed with bed true but left out of songs_per_10min and change_at, and aggregate leaves it out of
the pack's songs. A long quiet piece is a song played low under the talking (links does that for minutes), not a bed.

Writes DIR/measure_music.json (measure_music/1, no text from the film) and DIR/measure_music.embed.json
(CLAP vectors per window and per song, for the pack's music profile). Every conclusion is a measurement:
the agent does not listen (SKILL.md section 2).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import music_lib as ml  # noqa: E402
import stats  # noqa: E402

SCHEMA = "measure_music/1"
EMBED_SCHEMA = "measure_music_embed/1"
OUT = "measure_music.json"
EMBED_OUT = "measure_music.embed.json"
WINDOW_S = 10

MUSIC_PROMPTS = ["instrumental background music", "a song with melody", "piano music", "ambient music pad",
                 "orchestral strings music", "acoustic guitar music", "electronic music with a beat"]
OTHER_PROMPTS = ["wind noise", "people talking", "city street noise", "footsteps on snow", "ocean waves", "silence",
                 "a car engine", "birds and nature sounds", "crowd chatter", "water flowing"]
SING_PROMPTS = ["a person singing", "a song with vocals", "a singer and a melody"]
TALK_PROMPTS = ["a person talking", "a man speaking", "a woman speaking", "narration voice over"]

MUSIC_MIN = 0.1     # CLAP music-vs-ambient margin for a window to count as music
LEVEL_MIN = -50.0   # accompaniment below this (window median) is no music
VOICE_MIN = -45.0   # vocals stem below this: nobody singing or talking
QUIET_DB = -60.0    # a window this quiet is not embedded at all
CTX = 3             # windows on each side when comparing the music before and after an edge
CHANGE_COS = 0.70   # below this, with a change candidate within NEAR_S, a new song starts
LONE_COS = 0.55     # below this a new song starts even without a candidate
SURE_COS = 0.65     # a boundary is sure below this with a candidate within SURE_NEAR_S
NEAR_S, SURE_NEAR_S = 6.0, 3.0
MIN_SONG_S = 20.0
BRIDGE_COS = 0.80   # one non-music window between two runs is the same song when the music around it matches
CHAPTER_NEAR_S = 6.0
BODY_DB = 8.0       # within this of the film's usual music level, the music is at full level
BED_MAX_S = 30.0    # a quiet piece shorter than this is a bed, not a song


def log(msg: str) -> None:
    print(f"[measure_music] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- pure analysis (lists only, tested with stdlib)


def dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def unit_mean(vecs):
    vs = [v for v in vecs if v is not None]
    if not vs:
        return None
    m = [sum(c) / len(vs) for c in zip(*vs)]
    n = math.sqrt(dot(m, m))
    return [x / n for x in m] if n else None


def overlap(a: float, b: float, spans) -> float:
    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)


def candidates_of(music: dict | None) -> list[float]:
    """measure_audio's change candidates as times: both ends of silences and beat gaps, tempo changes."""
    music = music or {}
    out = [x for a, b in music.get("silences") or [] for x in (a, b)]
    out += [x for a, b in music.get("beat_gaps") or [] for x in (a, b)]
    out += [c["t"] for c in music.get("tempo_changes") or []]
    return sorted(float(x) for x in out)


def runs_of(flags: list[bool]) -> list[list[int]]:
    """[start, end) window index ranges of consecutive True."""
    out: list[list[int]] = []
    for i, f in enumerate(flags):
        if f and out and out[-1][1] == i:
            out[-1][1] = i + 1
        elif f:
            out.append([i, i + 1])
    return out


def bridge(runs: list[list[int]], vecs) -> list[list[int]]:
    """Join two runs across a single non-music window when the music on both sides matches."""
    out: list[list[int]] = []
    for r in runs:
        if out and r[0] - out[-1][1] == 1:
            a = unit_mean(vecs[max(out[-1][0], out[-1][1] - CTX):out[-1][1]])
            b = unit_mean(vecs[r[0]:min(r[1], r[0] + CTX)])
            if a and b and dot(a, b) >= BRIDGE_COS:
                out[-1][1] = r[1]
                continue
        out.append(list(r))
    return out


def ctx_cos(vecs, i: int, lo: int, hi: int, k: int = CTX) -> float | None:
    a, b = unit_mean(vecs[max(lo, i - k):i]), unit_mean(vecs[i:min(hi, i + k)])
    return dot(a, b) if a and b else None


def song_edges(lo: int, hi: int, vecs, cands: list[float], window_s: float = WINDOW_S) -> list[dict]:
    """Song changes inside one run of music windows; within 30 s only the strongest one counts."""
    edges = []
    for i in range(lo + 1, hi):
        c = ctx_cos(vecs, i, lo, hi)
        if c is None:
            continue
        t = i * window_s
        near = min(cands, key=lambda x: abs(x - t)) if cands else None
        d = abs(near - t) if near is not None else math.inf
        if (c < CHANGE_COS and d <= NEAR_S) or c < LONE_COS:
            edges.append({"i": i, "t": round(near if d <= NEAR_S else t, 2), "cos": round(c, 3),
                          "cand_s": round(d, 1) if d <= NEAR_S else None, "sure": c < SURE_COS and d <= SURE_NEAR_S})
    kept: list[dict] = []
    for e in sorted(edges, key=lambda e: e["cos"]):
        if all(abs(e["i"] - k["i"]) > CTX for k in kept):
            kept.append(e)
    return sorted(kept, key=lambda e: e["i"])


def segment(runs: list[list[int]], vecs, cands: list[float], duration: float,
            window_s: float = WINDOW_S) -> tuple[list[dict], list[dict], list[dict]]:
    """Runs -> songs. Returns (songs, stings, edges). Each song: t0, t1, i0, i1 (windows), start_sure."""
    songs, stings, all_edges = [], [], []
    for lo, hi in runs:
        t_lo, t_hi = lo * window_s, min(duration, hi * window_s)
        if t_hi - t_lo < MIN_SONG_S:
            stings.append({"t0": t_lo, "t1": round(t_hi, 2)})
            continue
        edges = song_edges(lo, hi, vecs, cands, window_s)
        all_edges += edges
        bounds = [(t_lo, lo, True)] + [(e["t"], e["i"], e["sure"]) for e in edges] + [(t_hi, hi, None)]
        segs = [{"t0": a[0], "t1": b[0], "i0": a[1], "i1": b[1], "start_sure": a[2]} for a, b in zip(bounds, bounds[1:])]
        while len(segs) > 1:
            short = [j for j, s in enumerate(segs) if s["t1"] - s["t0"] < MIN_SONG_S]
            if not short:
                break
            j = short[0]
            here = unit_mean(vecs[segs[j]["i0"]:segs[j]["i1"]])

            def sim(k):
                there = unit_mean(vecs[segs[k]["i0"]:segs[k]["i1"]])
                return dot(here, there) if here and there else -1.0
            k = max((x for x in (j - 1, j + 1) if 0 <= x < len(segs)), key=sim)
            a, b = sorted((j, k))
            segs[a:b + 1] = [{"t0": segs[a]["t0"], "t1": segs[b]["t1"], "i0": segs[a]["i0"], "i1": segs[b]["i1"],
                              "start_sure": segs[a]["start_sure"]}]
        songs += segs
    return songs, stings, all_edges


def power_mean(xs: list[float]) -> float | None:
    return 10 * math.log10(sum(10 ** (x / 10) for x in xs) / len(xs)) if xs else None


def fade_in_of(acc: list[float], start: int, span: int = 15) -> float:
    """Seconds from the music's first audible second until it is within 3 dB of where it gets to in `span` s."""
    seg = acc[start:start + span]
    if len(seg) < 3:
        return 0.0
    sm = [power_mean(seg[max(0, j - 1):j + 2]) for j in range(len(seg))]
    top = max(sm)
    return float(next(j for j, v in enumerate(sm) if v >= top - 3))


def fade_out_of(acc: list[float], end: int, span: int = 15) -> float:
    return fade_in_of(list(reversed(acc[max(0, end - span):end])), 0, span)


def body_of(rows: list[dict], is_music: list[bool], level: float | None, window_s: float = WINDOW_S) -> float | None:
    """Start of the first two music windows in a row within BODY_DB of `level` (the film's usual music level)."""
    if level is None:
        return None
    full = [m and r["acc_db"] >= level - BODY_DB for r, m in zip(rows, is_music)]
    return next((i * window_s for i in range(len(full) - 1) if full[i] and full[i + 1]), None)


def tags_of(vec, tag_keys, tag_vecs) -> list[dict]:
    if vec is None:
        return []
    sims = [dot(vec, t) for t in tag_vecs]
    out = []
    for cat in ml.TAGS:
        top = sorted(((s, k) for s, k in zip(sims, tag_keys) if k[0] == cat), reverse=True)[:3]
        out += [{"cat": cat, "tag": en, "zh": zh, "score": round(s, 3)} for s, (_, en, zh) in top]
    return out


def build(raw: dict, measure_audio: dict, chapters: list[dict] | None = None, today: str | None = None,
          window_s: float = WINDOW_S, lyrics: bool = False) -> tuple[dict, dict]:
    """raw: voc, acc (per-second dB), windows [{music, sing}], vecs (unit vectors or None per window),
    tag_keys and tag_vecs (CLAP text vectors for music_lib.TAGS). lyrics: the labels saw lyric captions, the one
    piece of evidence from the picture that someone sings. Returns (measure_music, embed)."""
    voc, acc, vecs = raw["voc"], raw["acc"], raw["vecs"]
    duration = float(measure_audio.get("duration_s") or len(acc))
    speech = measure_audio.get("speech") or []
    w = int(window_s)
    rows = []
    for i, x in enumerate(raw["windows"]):
        a, b = i * w, (i + 1) * w
        rows.append({"t0": a, "music": x.get("music"), "sing": x.get("sing"),
                     "acc_db": round(ml.pct(acc[a:b], 0.5), 1) if acc[a:b] else -90.0,
                     "voc_db": round(ml.pct(voc[a:b], 0.5), 1) if voc[a:b] else -90.0,
                     "speech": round(overlap(a, b, speech) / window_s, 2)})
    is_music = [r["music"] is not None and r["music"] > MUSIC_MIN and r["acc_db"] > LEVEL_MIN for r in rows]
    sung_raw = [r["sing"] is not None and r["sing"] > 0 and r["voc_db"] > VOICE_MIN for r in rows]
    # singing counts only in runs of two windows or more: a lone window is too easily a shout or a laugh
    sung = [sung_raw[i] and ((i > 0 and sung_raw[i - 1]) or (i + 1 < len(rows) and sung_raw[i + 1]))
            for i in range(len(rows))]
    runs = bridge(runs_of(is_music), vecs)
    cands = candidates_of(measure_audio.get("music"))
    songs, stings, edges = segment(runs, vecs, cands, duration, window_s)

    secs = range(min(len(acc), len(voc), len(rows) * w))
    in_music = [i for i in secs if is_music[i // w]]
    talk = [i for i in secs if overlap(i, i + 1, speech) >= 0.5 and not sung[i // w]]
    talk_set = set(talk)
    music_set = set(in_music)
    talk_in = [acc[i] for i in talk if i in music_set]
    free_in = [acc[i] for i in in_music if i not in talk_set]
    usual = free_in or [acc[i] for i in in_music]
    usual_db = ml.pct(usual, 0.5) if usual else None

    beats = (measure_audio.get("beats") or {}).get("beats") or []
    cuts = [float(c["t0"]) for c in chapters or [] if float(c.get("t0") or 0) > 0]
    out_songs, song_vecs = [], []
    for k, s in enumerate(songs):
        idx = range(s["i0"], s["i1"])
        mus = [i for i in idx if is_music[i]]
        vec = unit_mean([vecs[i] for i in mus])
        share = sum(1 for i in mus if sung[i]) / len(mus) if mus else None
        lv = [acc[i] for i in in_music if s["t0"] <= i < s["t1"]]
        level = ml.pct(lv, 0.5) if lv else None
        song = {"t0": round(s["t0"], 2), "t1": round(s["t1"], 2),
                "level_db": None if level is None else round(level, 1),
                "bed": (level is not None and usual_db is not None and level < usual_db - BODY_DB
                        and s["t1"] - s["t0"] < BED_MAX_S),
                "bpm": stats.bpm_from_beats(beats, spans=[[s["t0"], s["t1"]]]),
                "vocal_share": None if share is None else round(share, 2),
                "verdict": None if share is None else "vocal" if share >= 0.3 else "instrumental" if share <= 0.05
                else "mixed",
                "tags": tags_of(vec, raw.get("tag_keys") or [], raw.get("tag_vecs") or []),
                "at_chapter_cut": None if k == 0 else any(abs(s["t0"] - c) <= CHAPTER_NEAR_S for c in cuts),
                "start_sure": s["start_sure"]}
        out_songs.append(song)
        song_vecs.append(None if vec is None else [round(x, 5) for x in vec])

    mains = [s for s in out_songs if not s["bed"]]
    changes = [s["at_chapter_cut"] for s in mains[1:] if s["at_chapter_cut"] is not None]
    change_at = ("none" if len(mains) < 2 else "chapter" if cuts and changes and sum(changes) / len(changes) >= 0.6
                 else "within_chapter")
    first = next((i for i in in_music), None)
    last = in_music[-1] + 1 if in_music else None
    enter = None if first is None else next((i for i in in_music if acc[i] > LEVEL_MIN), first)
    exit_ = None if last is None else next((i + 1 for i in reversed(in_music) if acc[i] > LEVEL_MIN), last)
    duck = (round(ml.pct(free_in, 0.5) - ml.pct(talk_in, 0.5), 1)
            if len(talk_in) >= 20 and len(free_in) >= 20 else None)
    body = body_of(rows, is_music, usual_db, window_s)
    doubts = []
    if lyrics and not any(s["verdict"] in ("vocal", "mixed") for s in mains):
        doubts.append("lyric captions on screen but no song measured as sung: vocal_share may be too low")
    ambient = sum(1 for i in secs if not is_music[i // w] and i not in talk_set
                  and (acc[i] > LEVEL_MIN or voc[i] > VOICE_MIN))
    curve = []
    for p in range(10):
        a, b = int(duration * p / 10), int(duration * (p + 1) / 10)
        curve.append(power_mean([acc[i] if i in music_set else -90.0 for i in range(a, min(b, len(acc)))] or [-90.0]))
    top = max(curve)
    res = {
        "schema": SCHEMA, "date": today or dt.date.today().isoformat(), "clap": ml.CLAP_MODEL,
        "demucs": ml.DEMUCS_MODEL, "window_s": window_s, "duration_s": round(duration, 2),
        "coverage": round(sum(min(duration, (i + 1) * window_s) - i * window_s
                              for i in range(len(rows)) if is_music[i]) / duration, 3) if duration else None,
        "enter_s": enter, "body_s": body, "exit_s": exit_,
        "fade_in_s": None if enter is None else fade_in_of(acc, enter),
        "fade_out_s": None if exit_ is None else fade_out_of(acc, exit_),
        "under_speech": round(sum(1 for i in talk if i in music_set) / len(talk), 3) if talk else None,
        "duck_db": duck, "talk_s": len(talk), "ambient_only_s": ambient,
        "usual_db": None if usual_db is None else round(usual_db, 1),
        "songs": out_songs, "stings": stings,
        "bed_s": round(sum(s["t1"] - s["t0"] for s in out_songs if s["bed"]), 1),
        "songs_per_10min": round(len(mains) / (duration / 600), 2) if duration else None,
        "change_at": change_at,
        "energy_curve": [round(c - top, 1) for c in curve],
        "edges": edges,
        "doubts": doubts,
        "windows": rows,
    }
    embed = {"schema": EMBED_SCHEMA, "model": ml.CLAP_MODEL, "window_s": window_s,
             "windows": [None if v is None else [round(x, 4) for x in v] for v in vecs], "songs": song_vecs,
             "song_s": [round(s["t1"] - s["t0"], 2) for s in out_songs],
             "acc_s": acc, "voc_s": voc}  # per-second stem levels, so --rebuild needs no demucs
    return res, embed


# ---------------------------------------------------------------- measuring (demucs, CLAP; via uv)


def analyse(audio: Path) -> dict:
    import numpy as np
    import soxr

    mt = ml.clap_text(MUSIC_PROMPTS + OTHER_PROMPTS)
    st = ml.clap_text(SING_PROMPTS + TALK_PROMPTS)
    tag_keys = [(cat, en, zh) for cat, items in ml.TAGS.items() for en, zh in items]
    tag_vecs = ml.clap_text([ml.PROMPT[c].format(en) for c, en, _ in tag_keys])
    nm, ns = len(MUSIC_PROMPTS), len(SING_PROMPTS)
    voc: list[float] = []
    acc: list[float] = []
    windows: list[dict] = []
    vecs: list = []
    w48 = WINDOW_S * 48000
    for t0, sr, sources, stems in ml.separate(audio):
        vi = sources.index("vocals")
        db = ml.per_second_db(stems, sr)
        voc += [round(float(v), 1) for v in db[vi]]
        acc += [round(float(v), 1) for v in np.delete(db, vi, axis=0).max(axis=0)]
        a48 = soxr.resample(np.delete(stems, vi, axis=0).sum(axis=0), sr, 48000)
        v48 = soxr.resample(stems[vi], sr, 48000)
        n = len(a48) // w48
        if n == 0:
            continue
        ca = [a48[j * w48:(j + 1) * w48] for j in range(n)]
        cv = [v48[j * w48:(j + 1) * w48] for j in range(n)]
        ea, ev = ml.clap_audio(ca), ml.clap_audio(cv)
        for j in range(n):
            loud = 20 * np.log10(np.sqrt(np.mean(ca[j] ** 2)) + 1e-12) > QUIET_DB
            sa, sv = ea[j] @ mt.T, ev[j] @ st.T
            windows.append({"music": round(float(sa[:nm].max() - sa[nm:].max()), 3) if loud else None,
                            "sing": round(float(sv[:ns].max() - sv[ns:].max()), 3)})
            vecs.append([float(x) for x in ea[j]] if loud else None)
        log(f"{t0 + 60:.0f} s")
    return {"voc": voc, "acc": acc, "windows": windows, "vecs": vecs, "tag_keys": tag_keys,
            "tag_vecs": [[float(x) for x in r] for r in tag_vecs]}


def saved_raw(d: Path) -> dict:
    """The last run's levels, window scores and vectors, for --rebuild."""
    res = json.loads((d / OUT).read_text(encoding="utf-8"))
    emb = json.loads((d / EMBED_OUT).read_text(encoding="utf-8"))
    if "acc_s" not in emb or emb.get("window_s") != WINDOW_S:
        raise SystemExit(f"{d.name}: {EMBED_OUT} has no per-second levels for this window size; measure again")
    tag_keys = [(cat, en, zh) for cat, items in ml.TAGS.items() for en, zh in items]
    tag_vecs = ml.clap_text([ml.PROMPT[c].format(en) for c, en, _ in tag_keys])
    return {"voc": emb["voc_s"], "acc": emb["acc_s"], "vecs": emb["windows"],
            "windows": [{"music": w["music"], "sing": w["sing"]} for w in res["windows"]],
            "tag_keys": tag_keys, "tag_vecs": [[float(x) for x in r] for r in tag_vecs]}


def lyric_captions(labels: dict) -> bool:
    return any("lyric" in str(fx.get("type", "")) for fx in labels.get("effects") or [])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path)
    ap.add_argument("--audio", type=Path, help="the reference's sound, when DIR has no music44k.wav")
    ap.add_argument("--rebuild", action="store_true", help="redo the analysis from the last run's saved numbers")
    args = ap.parse_args(argv)
    d = args.dir.expanduser().resolve()
    ma_path = d / "measure_audio.json"
    if not ma_path.exists():
        raise SystemExit(f"{d.name}: no measure_audio.json; run measure_audio.py first")
    ma = json.loads(ma_path.read_text(encoding="utf-8"))
    lab_path = d / "labels.json"
    labels = json.loads(lab_path.read_text(encoding="utf-8")) if lab_path.exists() else {}
    if args.rebuild:
        raw = saved_raw(d)
    else:
        audio = (args.audio.expanduser() if args.audio else d / "music44k.wav")
        if not audio.exists():
            raise SystemExit(f"{d.name}: no audio at {audio.name}; pass --audio with the reference's video or sound")
        log(f"{d.name}: separating and embedding {audio.name}")
        raw = analyse(audio)
    dur = float(ma.get("duration_s") or 0)
    if dur and dur - len(raw["acc"]) > max(5.0, 0.02 * dur):  # learnings 2026-11-14: a download with its sound cut short
        raise SystemExit(f"{d.name}: {len(raw['acc'])} s of sound for a {dur:.0f} s film; the audio file is cut short "
                         "(check its audio stream with ffprobe), nothing written")
    res, embed = build(raw, ma, labels.get("chapters"), lyrics=lyric_captions(labels))
    ml.write_json(d / OUT, res)
    ml.write_json(d / EMBED_OUT, embed)
    sung = [s["verdict"] for s in res["songs"]]
    log(f"done: music {res['coverage']:.0%} of the film, {len(res['songs'])} songs ({', '.join(map(str, sung))}), "
        f"{len(res['stings'])} stings, enters {res['enter_s']} s, full from {res['body_s']} s, duck {res['duck_db']} dB, change at "
        f"{res['change_at']} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

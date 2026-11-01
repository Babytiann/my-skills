#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a ducked, music-only bed for a cut plan.

Resolve's API has no volume automation, and MCP mix_plan renders a full mix
(voice + music), so the bed is baked here: the music drops by `duck_db`
wherever the plan has speech, with linear attack/release ramps; speech spans
closer than `hold` seconds merge into one dip. apply puts the bed on A2 and the
raw music on a muted A3, so the dip can still be redone by hand in Fairlight.

Output: <plan dir>/music_ducked_<id>.wav and <plan dir>/ducked.json (relative
paths only).

Usage:
  python3 -I -B duck.py --project NAME [--duck-db 12] [--attack 0.15] [--release 0.4] [--hold 0.6]
  python3 -I -B duck.py --plan cut_plan.json [...]
"""

from __future__ import annotations

import argparse
import array
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402

ENV_RATE = 1000  # envelope samples per second


def merge(spans: list[tuple[float, float]], hold: float) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] < hold:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def envelope(spans, total_s: float, duck_db: float, attack: float, release: float,
             hold: float, rate: int = ENV_RATE) -> array.array:
    """Linear gain per sample: 1.0 outside speech, 10^(-duck_db/20) inside."""
    n = max(1, round(total_s * rate))
    depth = [0.0] * n
    for a, b in merge(spans, hold):
        lo = max(0, int((a - attack) * rate))
        hi = min(n, int((b + release) * rate) + 1)
        for i in range(lo, hi):
            t = i / rate
            if t < a:
                d = (t - (a - attack)) / attack if attack > 0 else 1.0
            elif t <= b:
                d = 1.0
            else:
                d = 1.0 - (t - b) / release if release > 0 else 0.0
            if d > depth[i]:
                depth[i] = min(1.0, max(0.0, d))
    floor = 10 ** (-duck_db / 20)
    return array.array("f", (1.0 - d * (1.0 - floor) for d in depth))


def probe_audio(path: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=sample_rate,channels", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    s = json.loads(r.stdout)["streams"][0]
    return int(s["sample_rate"]), int(s["channels"])


def render(music: Path, out: Path, start_s: float, dur_s: float, env: array.array) -> None:
    sr, ch = probe_audio(music)
    pan = f"pan={ch}c|" + "|".join(f"c{i}=c0" for i in range(ch))
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "env.f32"
        if sys.byteorder != "little":
            env = array.array("f", env)
            env.byteswap()
        with open(raw, "wb") as fh:
            env.tofile(fh)
        graph = (f"[1:a]aresample={sr},{pan},aformat=sample_fmts=fltp[e];"
                 f"[0:a]aformat=sample_fmts=fltp:sample_rates={sr}[m];[m][e]amultiply,"
                 f"atrim=duration={dur_s:.6f}[out]")
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{start_s:.6f}", "-t", f"{dur_s:.6f}", "-i", str(music),
               "-f", "f32le", "-ar", str(ENV_RATE), "-ac", "1", "-i", str(raw),
               "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s24le", str(out)]
        subprocess.run(cmd, check=True)


def duck_plan(plan_path: Path, P: vpaths.Paths, duck_db: float, attack: float,
              release: float, hold: float) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    fps = plan["timeline"]["fps"]
    speech = [(a / fps, b / fps) for a, b in plan.get("speech") or []]
    beds = []
    for m in plan.get("audio") or []:
        if m.get("kind") != "music":
            continue
        t0, t1 = m["rec_in"] / fps, m["rec_out"] / fps
        local = [(max(a, t0) - t0, min(b, t1) - t0) for a, b in speech if b > t0 and a < t1]
        env = envelope(local, t1 - t0, duck_db, attack, release, hold)
        out = plan_path.parent / f"music_ducked_{m['id']}.wav"
        render(P.abs(m["source"]), out, float(m.get("src_in_s") or 0), t1 - t0, env)
        beds.append({"music": m["id"], "bed": P.rel(out), "dips": len(merge(local, hold)),
                     "rec_in": m["rec_in"], "rec_out": m["rec_out"]})
    report = {"duck_db": duck_db, "attack_s": attack, "release_s": release, "hold_s": hold, "beds": beds}
    (plan_path.parent / "ducked.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--duck-db", type=float, default=12.0)
    ap.add_argument("--attack", type=float, default=0.15)
    ap.add_argument("--release", type=float, default=0.4)
    ap.add_argument("--hold", type=float, default=0.6)
    args = ap.parse_args(argv)
    if not shutil.which("ffmpeg"):
        print("duck: ffmpeg not on PATH", file=sys.stderr)
        return 2
    P = vpaths.load()
    plan = args.plan or (P.project(args.project) / "plan" / "cut_plan.json" if args.project else None)
    if not plan:
        ap.error("need --project or --plan")
    print(json.dumps(duck_plan(plan, P, args.duck_db, args.attack, args.release, args.hold),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

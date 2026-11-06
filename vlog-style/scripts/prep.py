#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch, probe and proxy reference videos and footage (ffmpeg, stdlib only).

Every measurement runs on a proxy: 540p, native frame rate (cut timing and
speed checks need every frame), H.264 with a 1 s GOP so frames can be grabbed
quickly. VideoToolbox does the decode/encode on Apple Silicon (4:2:2 10-bit
HEVC/H.264 included); without it ffmpeg falls back to libx264.

Usage:
  python3 -I -B prep.py probe FILE
  python3 -I -B prep.py ref BV_OR_URL_OR_FILE --id ID [--quality 1080p]
  python3 -I -B prep.py footage --project NAME
  python3 -I -B prep.py proxy SRC --out DIR

`ref` downloads Bilibili videos with `opencli bilibili download` (logged-in
Chrome session + yt-dlp) into <root>/cache/refs/<id>/src/, then proxies them.
Paid or 充电 videos stop with PAID_CONTENT; this script never passes --force.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402

PROXY_HEIGHT = 540
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".mts", ".mxf", ".avi", ".webm", ".flv"}
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
DOWNLOAD_TIMEOUT_S = 3600
# opencli stops waiting for a browser command after 60 s unless told otherwise, yet lets the
# command run on: a longer download leaves a complete file and still exits non-zero.
OPENCLI_TIMEOUT_ENV = "OPENCLI_BROWSER_COMMAND_TIMEOUT"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def rate(s: str | None) -> float | None:
    try:
        f = Fraction(s)
        return round(float(f), 3) if f else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    if r.returncode:
        raise SystemExit(f"ffprobe failed on {path.name}: {r.stderr.strip()[:300]}")
    data = json.loads(r.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        raise SystemExit(f"{path.name}: no video stream")
    fps, avg = rate(v.get("r_frame_rate")), rate(v.get("avg_frame_rate"))
    dur = float(v.get("duration") or data["format"].get("duration") or 0)
    pix = v.get("pix_fmt") or ""
    chroma = "422" if "422" in pix else "444" if "444" in pix else "420" if pix else None
    out = {
        "codec": v.get("codec_name"), "profile": v.get("profile"), "pix_fmt": pix,
        "bit_depth": 10 if re.search(r"p10|p210|10le|10be", pix) else 8 if pix else None,
        "chroma": chroma, "width": v.get("width"), "height": v.get("height"),
        "fps": fps, "avg_fps": avg,
        "vfr": bool(fps and avg and abs(fps - avg) / fps > 0.01),
        "ntsc": bool(fps and abs(fps - round(fps)) > 0.01),
        "duration_s": round(dur, 3),
        "frames": int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else round(dur * (fps or 0)),
        "color_transfer": v.get("color_transfer"), "color_primaries": v.get("color_primaries"),
        "color_space": v.get("color_space"),
        "hdr": v.get("color_transfer") in ("arib-std-b67", "smpte2084"),
        "rotation": int(float((v.get("tags") or {}).get("rotate", 0) or 0)),
        "audio": None if a is None else {"codec": a.get("codec_name"), "channels": a.get("channels"),
                                         "sample_rate": int(a.get("sample_rate") or 0)},
    }
    return out


def _videotoolbox() -> bool:
    r = run(["ffmpeg", "-hide_banner", "-hwaccels"])
    return "videotoolbox" in r.stdout


def proxy(src: Path, out_dir: Path, info: dict, height: int = PROXY_HEIGHT) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "proxy.mp4"
    fps = info["fps"] or 30
    gop = str(max(1, round(fps)))
    vf = f"scale=-2:{height}:flags=bilinear,format=yuv420p"
    cfr = ["-fps_mode", "cfr", "-r", str(Fraction(fps).limit_denominator(1001))] if info["vfr"] else []
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    tries = []
    if _videotoolbox():
        tries.append(base + ["-hwaccel", "videotoolbox", "-i", str(src), "-map", "0:v:0", "-vf", vf, *cfr,
                             "-c:v", "h264_videotoolbox", "-b:v", "6M", "-g", gop, "-an", str(dst)])
    tries.append(base + ["-i", str(src), "-map", "0:v:0", "-vf", vf, *cfr,
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", gop, "-an", str(dst)])
    err = ""
    for cmd in tries:
        r = run(cmd)
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            break
        err = r.stderr.strip()[-400:]
    else:
        raise SystemExit(f"proxy failed for {src.name}: {err}")
    audio = None
    if info["audio"]:
        r = run(base + ["-i", str(src), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                        str(out_dir / "asr16k.wav"), "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "44100",
                        "-c:a", "pcm_s16le", str(out_dir / "music44k.wav")])
        if r.returncode:
            raise SystemExit(f"audio extract failed for {src.name}: {r.stderr.strip()[-300:]}")
        audio = {"asr": "asr16k.wav", "music": "music44k.wav"}
    p = probe(dst)
    return {"file": "proxy.mp4", "fps": p["fps"], "width": p["width"], "height": p["height"],
            "frames": p["frames"], "duration_s": p["duration_s"], "audio": audio}


def describe_source(P: vpaths.Paths, src: Path) -> dict:
    """Relative path when under root; otherwise only the file name."""
    try:
        return {"path": P.rel(src)}
    except ValueError:
        return {"path": None, "name": src.name, "external": True}


def prep_one(P: vpaths.Paths, src: Path, out_dir: Path, extra: dict | None = None) -> dict:
    info = probe(src)
    report = {"source": describe_source(P, src), "probe": info, **(extra or {})}
    report["proxy"] = proxy(src, out_dir, info)
    (out_dir / "prep.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def opencli_json(args: list[str], timeout: int = 1800) -> tuple[object | None, str]:
    if not shutil.which("opencli"):
        return None, "opencli not on PATH"
    r = run(["opencli", "bilibili", *args, "-f", "json"], timeout=timeout)
    if r.returncode:
        return None, (r.stderr or r.stdout).strip()[-400:]
    try:
        return json.loads(r.stdout), ""
    except json.JSONDecodeError:
        return None, r.stdout.strip()[-400:]


def fetch_bilibili(P: vpaths.Paths, bv: str, ref_dir: Path, quality: str) -> tuple[Path, dict]:
    src_dir = ref_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    meta, err = opencli_json(["video", bv], timeout=120)
    if meta is not None:
        (ref_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    def videos():
        return sorted((p for p in src_dir.rglob("*") if p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")),
                      key=lambda p: p.stat().st_size, reverse=True)

    if not videos():  # re-running prep keeps an earlier download
        # download prints progress, not JSON: judge it by exit code and the file it leaves
        env = {OPENCLI_TIMEOUT_ENV: str(DOWNLOAD_TIMEOUT_S), **os.environ}
        r = run(["opencli", "bilibili", "download", bv, "--output", str(src_dir), "--quality", quality],
                timeout=DOWNLOAD_TIMEOUT_S, env=env)
        if r.returncode or not videos():
            # opencli reports errors on stdout and progress on stderr
            raise SystemExit(f"download failed for {bv} (exit {r.returncode})\n"
                             f"stdout: {r.stdout.strip()[-400:]}\nstderr: {r.stderr.strip()[-400:]}")
    files = videos()
    subs, _ = opencli_json(["subtitle", bv, "--lang", "ai-zh"], timeout=120)
    if subs:
        (ref_dir / "subtitles.json").write_text(json.dumps(subs, ensure_ascii=False, indent=1), encoding="utf-8")
    extra = {"bvid": bv, "url": f"https://www.bilibili.com/video/{bv}", "quality": quality,
             "platform_subtitles": bool(subs)}
    return files[0], extra


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("file", type=Path)
    r = sub.add_parser("ref")
    r.add_argument("source")
    r.add_argument("--id", required=True)
    r.add_argument("--quality", default="1080p")
    f = sub.add_parser("footage")
    f.add_argument("--project", required=True)
    x = sub.add_parser("proxy")
    x.add_argument("src", type=Path)
    x.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("prep: ffmpeg/ffprobe not on PATH", file=sys.stderr)
        return 2

    if args.cmd == "probe":
        print(json.dumps(probe(args.file), ensure_ascii=False, indent=1))
        return 0
    P = vpaths.load()
    if args.cmd == "proxy":
        rep = prep_one(P, args.src, args.out)
    elif args.cmd == "ref":
        ref_dir = P.ref(args.id)
        local = Path(args.source).expanduser()
        if local.exists():
            rep = prep_one(P, local, ref_dir)
        else:
            m = BV_RE.search(args.source)
            if not m:
                raise SystemExit("ref: give a local file, a BV id or a bilibili.com/video URL "
                                 "(resolve b23.tv short links first)")
            src, extra = fetch_bilibili(P, m.group(1), ref_dir, args.quality)
            rep = prep_one(P, src, ref_dir, extra)
    else:  # footage
        proj = P.project(args.project)
        clips = sorted(p for p in (proj / "footage").rglob("*") if p.suffix.lower() in VIDEO_EXT)
        if not clips:
            raise SystemExit(f"no video files under {P.rel(proj / 'footage')}")
        rep = {"clips": []}
        for c in clips:
            rel_id = vpaths.check_name(re.sub(r"[^\w.-]", "_", c.stem))
            out = proj / "proxies" / rel_id
            if (out / "prep.json").exists():
                one = json.loads((out / "prep.json").read_text(encoding="utf-8"))
            else:
                one = prep_one(P, c, out)
            rep["clips"].append({"id": rel_id, "source": one["source"]["path"], "proxy": P.rel(out),
                                 "fps": one["probe"]["fps"], "hdr": one["probe"]["hdr"],
                                 "vfr": one["probe"]["vfr"]})
            print(f"{rel_id}: {one['probe']['width']}x{one['probe']['height']} @ {one['probe']['fps']} "
                  f"{one['probe']['color_transfer']}", file=sys.stderr)
        (proj / "index").mkdir(exist_ok=True)
        (proj / "index" / "prep.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

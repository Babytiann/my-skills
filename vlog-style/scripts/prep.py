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
  python3 -I -B prep.py footage --project NAME     # videos and photos (jpg, jpeg, heic, heif, png)
  python3 -I -B prep.py proxy SRC --out DIR

`ref` downloads Bilibili videos with `opencli bilibili download` (logged-in
Chrome session + yt-dlp) into <root>/cache/refs/<id>/src/, then proxies them.
Paid or 充电 videos stop with PAID_CONTENT; this script never passes --force.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from itertools import chain, count
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402

PROXY_HEIGHT = 540
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".mts", ".mxf", ".avi", ".webm", ".flv"}
IMAGE_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png"}
RAW_EXT = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2"}  # listed for the user, not used yet
GENERIC_BRANDS = {"isom", "iso2", "iso4", "iso5", "iso6", "mp41", "mp42", "qt", "m4v", "avc1", "dash"}
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
    fmt_tags = (data.get("format") or {}).get("tags") or {}
    shot_at, shot_at_source = shot_time(fmt_tags, v.get("tags") or {})
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
        "shot_at": shot_at, "shot_at_source": shot_at_source, "device": device_of(fmt_tags),
    }
    return out


def parse_time(value) -> str | None:
    """A creation timestamp as UTC ISO text. A camera with no clock writes 1904 or 1970, which is no time at all."""
    try:
        t = dt.datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if t.year < 2000:
        return None
    t = t.replace(tzinfo=t.tzinfo or dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).isoformat()


def shot_time(fmt: dict, stream: dict) -> tuple[str | None, str | None]:
    """When the clip was shot, and which tag said so. Never the file's mtime: copying a card changes it.

    quicktime.creationdate comes first because an iPhone rewrites creation_time when a
    clip is trimmed or exported, and this one keeps the capture time and its zone.
    """
    for source, value in (("quicktime.creationdate", fmt.get("com.apple.quicktime.creationdate")),
                          ("format.creation_time", fmt.get("creation_time")),
                          ("stream.creation_time", stream.get("creation_time"))):
        iso = parse_time(value) if value else None
        if iso:
            return iso, source
    return None, None


def device_of(fmt: dict) -> str | None:
    """Which camera, from what the file says. Clock offsets are per device, so it only has to be consistent."""
    make = fmt.get("com.apple.quicktime.make") or fmt.get("make") or ""
    model = fmt.get("com.apple.quicktime.model") or fmt.get("model") or ""
    named = f"{make} {model}".strip()
    if named:
        return named
    encoder = fmt.get("encoder") or ""
    if encoder and not re.match(r"(?i)lav[fc]|ffmpeg", encoder):
        return encoder
    brand = (fmt.get("major_brand") or "").strip()
    return brand if brand and brand.lower() not in GENERIC_BRANDS else None


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


def hvc1_remux(src: Path) -> str | None:
    """Bilibili serves some HEVC tagged 'hev1'. ffmpeg plays it, AVFoundation (vision_ocr.swift)
    can't open it, so OCR would read nothing. Re-tag a download as 'hvc1' in place: a stream
    copy, so frames and timestamps stay the same. Returns what changed, or None."""
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "stream=index,codec_type,codec_name,codec_tag_string:stream_disposition=attached_pic",
             "-of", "json", str(src)])
    if r.returncode:
        return None  # not a readable video; probe() reports it properly later
    try:
        streams = json.loads(r.stdout).get("streams") or []
    except json.JSONDecodeError:
        return None
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    if not v or v.get("codec_name") != "hevc" or v.get("codec_tag_string") != "hev1":
        return None
    tmp = src.with_name(f".remux{src.suffix}")  # hidden, so videos() never picks it up
    r = run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0", "-c", "copy",
             f"-tag:{v['index']}", "hvc1", str(tmp)])
    if r.returncode:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"re-tagging {src.name} as hvc1 failed: {r.stderr.strip()[-300:]}")
    os.replace(tmp, src)
    return "hev1->hvc1"


def fetch_bilibili(P: vpaths.Paths, bv: str, ref_dir: Path, quality: str) -> tuple[Path, dict]:
    src_dir = ref_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    meta, err = opencli_json(["video", bv], timeout=120)
    if meta is not None:
        (ref_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    def videos():
        # .temp.mp4 is yt-dlp mid-way through embedding the cover: larger than the real file, and may be cut short
        return sorted((p for p in src_dir.rglob("*") if p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")
                       and not p.name.endswith(f".temp{p.suffix}")),
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
    if remuxed := hvc1_remux(files[0]):
        extra["remuxed"] = remuxed
    return files[0], extra


def footage_files(footage: Path, exts: set[str] = VIDEO_EXT | IMAGE_EXT) -> list[Path]:
    """Footage under footage/, minus anything hidden: exFAT cards put ._C0001.MP4 beside every clip."""
    return sorted(p for p in footage.rglob("*")
                  if p.suffix.lower() in exts and p.is_file()
                  and not any(part.startswith(".") for part in p.relative_to(footage).parts))


def clean_id(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name).lstrip("._-") or "clip"


def assign_ids(files: list[str], cached: dict[str, str]) -> dict[str, str]:
    """Clip id of each file (POSIX path relative to footage/).

    tags.json, story.json and the brief name clips by id, so an id never moves once
    a proxy exists: `cached` maps each existing id to the file it was made from. A
    stem nobody else uses is the id. Stems that collide take the nearest folder
    (cardB_C0001); a file directly in footage/ keeps the bare stem; two files in one
    folder, like a Live Photo's IMG_0001.HEIC and IMG_0001.MOV, take their extension.
    Compared case-insensitively because APFS is.
    """
    ids: dict[str, str] = {}
    for clip_id, f in sorted(cached.items()):
        if f in files:
            ids.setdefault(f, clip_id)
    taken = {clip_id.casefold() for clip_id in cached}
    stems: dict[str, list[PurePosixPath]] = {}
    for f in files:
        stems.setdefault(clean_id(PurePosixPath(f).stem).casefold(), []).append(PurePosixPath(f))
    for f in sorted(files):
        if f in ids:
            continue
        p = PurePosixPath(f)
        rivals = stems[clean_id(p.stem).casefold()]
        twins = [r for r in rivals if r != p and r.parent == p.parent]
        base = f"{clean_id(p.stem)}_{p.suffix[1:].lower()}" if twins else clean_id(p.stem)
        folders = [clean_id(d) for d in p.parent.parts]
        alone = all(r.parent == p.parent for r in rivals)  # only twins here, and the extension tells them apart
        names = [base] if alone or not folders else []
        names += ["_".join(folders[-k:] + [base]) for k in range(1, len(folders) + 1)]
        tries = chain(names, (f"{names[-1]}_{i}" for i in count(2)))
        ids[f] = next(n for n in tries if n.casefold() not in taken)
        taken.add(ids[f].casefold())
    return ids


# ---------------------------------------------------------------- photos

def exif_fields(jpeg: bytes) -> dict:
    """The few EXIF tags a photo needs, from a JPEG's APP1 segment: stdlib only.

    sips reports `creation` from the TIFF DateTime tag only, and mdls gives the
    file's own creation time (a copy resets it), so neither knows when the
    picture was taken. sips does keep the EXIF when it writes the preview, so
    the preview is read here for every format.
    """
    want = {0x010F: "Make", 0x0110: "Model", 0x0112: "Orientation", 0x0132: "DateTime",
            0x9003: "DateTimeOriginal", 0x9011: "OffsetTimeOriginal"}
    i = 2
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF:
        marker, size = jpeg[i + 1], int.from_bytes(jpeg[i + 2:i + 4], "big")
        if marker == 0xE1 and jpeg[i + 4:i + 10] == b"Exif\0\0":
            tiff = jpeg[i + 10:i + 2 + size]
            break
        if marker in (0xDA, 0xD9):
            return {}
        i += 2 + size
    else:
        return {}
    order = "big" if tiff[:2] == b"MM" else "little"
    num = lambda at, n: int.from_bytes(tiff[at:at + n], order)  # noqa: E731
    out: dict = {}

    def walk(at: int) -> int | None:
        sub = None
        for k in range(num(at, 2) if at + 2 <= len(tiff) else 0):
            e = at + 2 + 12 * k
            tag, typ, cnt = num(e, 2), num(e + 2, 2), num(e + 4, 4)
            if tag == 0x8769:
                sub = num(e + 8, 4)
            elif tag in want and typ == 2:
                at_v = e + 8 if cnt <= 4 else num(e + 8, 4)
                out[want[tag]] = tiff[at_v:at_v + cnt].split(b"\0")[0].decode("ascii", "replace").strip()
            elif tag in want and typ == 3:
                out[want[tag]] = num(e + 8, 2)
        return sub

    sub = walk(num(4, 4))
    if sub:
        walk(sub)
    return out


def photo_time(fields: dict) -> tuple[str | None, str | None]:
    """When a photo was taken, as UTC ISO text, and how sure that is.

    EXIF keeps the camera's local wall time; OffsetTimeOriginal says which zone.
    Without it the time is read as this Mac's local time ("exif.no_zone"), which
    is wrong by the zone difference when the trip was abroad: prep lists such
    photos as their own device so one clock offset can put them right.
    """
    raw = fields.get("DateTimeOriginal") or fields.get("DateTime")
    try:
        t = dt.datetime.strptime(raw or "", "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, None
    if t.year < 2000:
        return None, None
    m = re.fullmatch(r"([+-])(\d\d):(\d\d)", fields.get("OffsetTimeOriginal") or "")
    if m:
        sign = 1 if m.group(1) == "+" else -1
        t = t.replace(tzinfo=dt.timezone(sign * dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))))
        source = "exif.DateTimeOriginal" if fields.get("DateTimeOriginal") else "exif.DateTime"
    else:
        t = t.astimezone()
        source = "exif.no_zone"
    return t.astimezone(dt.timezone.utc).isoformat(), source


def sips_props(path: Path, *keys: str) -> dict:
    r = run(["sips", *[x for k in keys for x in ("-g", k)], str(path)])
    if r.returncode:
        raise SystemExit(f"sips failed on {path.name}: {r.stderr.strip()[:300]}")
    props = {}
    for line in r.stdout.splitlines()[1:]:
        k, _, v = line.strip().partition(": ")
        if v and v != "<nil>":
            props[k] = v
    return props


def prep_photo(P: vpaths.Paths, src: Path, out_dir: Path, rel: str) -> dict:
    """A photo's prep.json: size, shooting time, camera, and a 640 px preview for tagging and review."""
    out_dir.mkdir(parents=True, exist_ok=True)
    props = sips_props(src, "pixelWidth", "pixelHeight", "make", "model")
    r = run(["sips", "-Z", "640", "-s", "format", "jpeg", str(src), "--out", str(out_dir / "preview.jpg")])
    if r.returncode:
        raise SystemExit(f"sips could not make a preview of {src.name}: {r.stderr.strip()[:300]}")
    fields = exif_fields((out_dir / "preview.jpg").read_bytes())
    shot_at, source = photo_time(fields)
    w, h = int(props.get("pixelWidth", 0)), int(props.get("pixelHeight", 0))
    if fields.get("Orientation") in (5, 6, 7, 8):  # stored sideways; ffmpeg turns it upright on decode
        w, h = h, w
    camera = f"{props.get('make', '')} {props.get('model', '')}".strip() or None
    base = camera or (rel.split("/")[0] if "/" in rel else "unknown")
    report = {"kind": "photo", "source": describe_source(P, src), "preview": "preview.jpg",
              "probe": {"format": src.suffix[1:].lower(), "width": w, "height": h,
                        "orientation": fields.get("Orientation"), "shot_at": shot_at, "shot_at_source": source,
                        "device": camera},
              "device": f"{base} (no time zone)" if source == "exif.no_zone" else base}
    (out_dir / "prep.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def refresh_prep(one: dict, src: Path, out: Path, rel: str) -> None:
    """Bring a prep.json from an older run up to date without redoing its proxy."""
    if one.get("kind") == "photo":
        return
    changed = False
    if "shot_at" not in one["probe"]:
        fresh = probe(src)
        one["probe"].update({k: fresh[k] for k in ("shot_at", "shot_at_source", "device")})
        changed = True
    if "device" not in one:  # no make or model in the file: the card's folder still tells cameras apart
        one["device"] = one["probe"]["device"] or (rel.split("/")[0] if "/" in rel else "unknown")
        changed = True
    if changed:
        (out / "prep.json").write_text(json.dumps(one, ensure_ascii=False, indent=1), encoding="utf-8")


def device_ranges(clips: list[dict]) -> list[dict]:
    """Each device's time range side by side, so a camera whose clock is off shows at a glance."""
    out = []
    for name in sorted({c["device"] for c in clips}):
        own = [c for c in clips if c["device"] == name]
        times = sorted(c["shot_at"] for c in own if c["shot_at"])
        out.append({"device": name, "clips": len(own), "first": times[0] if times else None,
                    "last": times[-1] if times else None, "undated": len(own) - len(times),
                    "sources": sorted({c["shot_at_source"] for c in own if c["shot_at_source"]})})
    return out


def prep_footage(P: vpaths.Paths, project: str) -> dict:
    proj = P.project(project)
    footage, proxies = proj / "footage", proj / "proxies"
    files = footage_files(footage)
    if not files:
        raise SystemExit(f"no video or photo files under {P.rel(footage)}")
    rel_of = {c: c.relative_to(footage).as_posix() for c in files}
    prefix = PurePosixPath(P.rel(footage))
    cached = {}
    for d in sorted(proxies.iterdir()) if proxies.exists() else []:
        if (d / "prep.json").exists():
            src = PurePosixPath(json.loads((d / "prep.json").read_text(encoding="utf-8"))["source"]["path"])
            cached[d.name] = src.relative_to(prefix).as_posix() if src.is_relative_to(prefix) else str(src)
    ids = assign_ids(list(rel_of.values()), cached)
    for clip_id, f in sorted(cached.items()):
        if f not in ids:
            print(f"warn proxies/{clip_id}: its source {f} is gone from footage/", file=sys.stderr)
    rep = {"clips": []}
    for c in files:
        rel_id = vpaths.check_name(ids[rel_of[c]])
        out = proxies / rel_id
        photo = c.suffix.lower() in IMAGE_EXT
        if (out / "prep.json").exists():
            one = json.loads((out / "prep.json").read_text(encoding="utf-8"))
        else:
            one = prep_photo(P, c, out, rel_of[c]) if photo else prep_one(P, c, out)
        if photo:
            rep["clips"].append({"id": rel_id, "kind": "photo", "source": one["source"]["path"], "proxy": P.rel(out),
                                 "device": one["device"], "shot_at": one["probe"]["shot_at"],
                                 "shot_at_source": one["probe"]["shot_at_source"]})
            print(f"{rel_id}: photo {one['probe']['width']}x{one['probe']['height']} "
                  f"{one['probe']['shot_at_source'] or 'no time'}", file=sys.stderr)
            continue
        refresh_prep(one, c, out, rel_of[c])
        rep["clips"].append({"id": rel_id, "source": one["source"]["path"], "proxy": P.rel(out),
                             "fps": one["probe"]["fps"], "hdr": one["probe"]["hdr"],
                             "vfr": one["probe"]["vfr"], "device": one["device"],
                             "shot_at": one["probe"]["shot_at"], "shot_at_source": one["probe"]["shot_at_source"]})
        print(f"{rel_id}: {one['probe']['width']}x{one['probe']['height']} @ {one['probe']['fps']} "
              f"{one['probe']['color_transfer']}", file=sys.stderr)
    zoneless = [c["id"] for c in rep["clips"] if c["shot_at_source"] == "exif.no_zone"]
    if zoneless:
        here = dt.datetime.now().astimezone().strftime("%z")
        print(f"warn {len(zoneless)} photo(s) carry no time zone and were read as this Mac's local time "
              f"(UTC{here[:3]}:{here[3:]}). If the trip was in another zone, compare their device range "
              "with the videos below and set clock_offsets_s for the '(no time zone)' device in the brief",
              file=sys.stderr)
    untimed = [c["id"] for c in rep["clips"] if c.get("kind") == "photo" and not c["shot_at"]]
    if untimed:
        print(f"warn {len(untimed)} photo(s) carry no shooting time in their EXIF, which happens when a photo is "
              "exported or sent through a chat app: use the camera original, or write shot_at for it in "
              "tags.json. Until then nothing can check where it belongs: " + ", ".join(untimed[:5])
              + (" ..." if len(untimed) > 5 else ""), file=sys.stderr)
    raw = footage_files(footage, RAW_EXT)
    if raw:
        rep["raw_skipped"] = [f.relative_to(footage).as_posix() for f in raw]
        print(f"warn {len(raw)} RAW photo(s) skipped (not supported yet); export them as jpg or heic: "
              + ", ".join(rep["raw_skipped"][:5]) + (" ..." if len(raw) > 5 else ""), file=sys.stderr)
    rep["devices"] = device_ranges(rep["clips"])
    for d in rep["devices"]:
        print(f"device {d['device']}: {d['clips']} clip(s), {d['first']} .. {d['last']}, "
              f"{d['undated']} undated, from {', '.join(d['sources']) or 'nothing'}", file=sys.stderr)
    (proj / "index").mkdir(exist_ok=True)
    (proj / "index" / "prep.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


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
        rep = prep_footage(P, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

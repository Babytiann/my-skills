#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measurements + Claude's labels -> draft style.json, or a footage index.

  python3 -I -B aggregate.py labels REF_DIR
      write REF_DIR/labels.json: every measured event, title and a chapter
      proposal, with blanks for Claude to fill after viewing sheets/ and strips/
  python3 -I -B aggregate.py draft --name NAME REF_DIR... [--out style.json] [--size 3840x2160] [--force]
      reconcile labels with measurements, pool the references (or, with one
      reference, its chapters) and write a schema-checked draft style.json;
      evidence (consistency, unverified and rejected labels) goes in style.evidence.
      REF_DIR is a cache entry or a kept copy (<root>/styles/<group>/refs/<id>).
      An existing --out is refused without --force: a finished style.json holds
      hand-written parts. Without --out the draft goes to REF_DIR/style.draft.json.
  python3 -I -B aggregate.py footage --project NAME
      index/footage.json for plan_cut.py from each clip's measurements plus
      Claude's index/tags.json

Reconciliation: times come from measurement. A label must point at a measured
item (event i, title track, shot i, or a chapter span); anything else is
rejected and listed, never silently used.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402
import schema  # noqa: E402
import stats  # noqa: E402

ROLES = ["hook", "intro", "chapter", "interlude", "ending"]
MOVES = ("digital", "camera")  # what made a measured push/pull: the edit, or the camera moving
META_KEYS = ("bvid", "title", "author", "mid", "publish_time", "duration_sec")  # all a kept meta.json holds
MEASURED_TO_TYPE = {"dissolve": "dissolve", "flash_white": "flash_white", "dip_to_black": "dip_to_black",
                    "whip": "whip", "zoom": "zoom", "hard_sample": "hard_cut"}
DEFAULT_FRAMES = {"dissolve": 12, "dip_to_black": 12, "flash_white": 6, "whip": 8, "zoom": 8,
                  "smooth_cut": 6, "wipe": 12, "other": 10}
FONT_FALLBACK = {"黑体": ["PingFang SC", "Hiragino Sans GB", "STHeiti"], "宋体": ["Songti SC", "STSong"],
                 "圆体": ["Yuanti SC", "PingFang SC"], "手写": ["Hannotate SC", "Kaiti SC"],
                 "楷体": ["Kaiti SC", "STKaiti"], "装饰体": ["PingFang SC"]}
ROUTES = {  # element -> (label, route, status, how); statuses from the Resolve 21.1.1.10 probe, 2026-11-02
    "hard_cut": ("剪辑点", "live_api", "verified", "append_to_timeline clipInfos，逐条读回"),
    "dissolve": ("叠化", "live_api", "verified", "add_transition Cross Dissolve，从后往前加"),
    "dip_to_black": ("黑场过渡", "live_api", "verified", "add_transition Dip To Color Dissolve，从后往前加"),
    "smooth_cut": ("Smooth Cut", "live_api", "verified", "add_transition Smooth Cut"),
    "flash_white": ("闪白", "fusion", "verified", "add_transition category=fusion Brightness Flash"),
    "whip": ("甩镜", "fusion", "verified", "add_transition category=fusion Pan Left/Right"),
    "zoom": ("急推转场", "fusion", "verified", "add_transition category=fusion Crash Zoom"),
    "wipe": ("划像", "live_api", "probe", "add_transition（具体名字待探针）"),
    "push_in": ("动态推拉", "fusion", "verified",
                "最小 .comp 模板（MediaIn→Transform→MediaOut，Size 关键帧，末帧=时长−1）+ timeline_item_fusion import_comp"),
    "slow_mo": ("慢动作", "live_api", "verified", "set_speed Percentage；60p 时间线加 set_retime optical_flow"),
    "ramp": ("变速曲线", "manual", "manual", "离线 .drt 本机否掉（速度全错），在 Resolve 里手动拉变速曲线"),
    "shake": ("持续抖动", "offline_drx", "verified", "Camera Shake OFX 节点并进 look DRX（drx merge）"),
    "letterbox": ("遮幅", "live_api", "verified", "set_output_blanking"),
    "title": ("地点标题/章节卡", "live_api", "verified",
              "单独时间线 insert_fusion_title，fusion_comp bulk_set_inputs 写 Template 文字和字体，再整条时间线放到 V3"),
    "subtitle": ("字幕", "live_api", "verified", "导入 .srt，用旧式 append 放到空时间线（按秒 × 时间线帧率落位）；set_subtitle_preset 用 Simple White，会新建时间线修订；导出时 ExportSubtitle + BurnIn 烧录"),
    "grade": ("调色", "live_api", "probe",
              "grade.py 每章一个 ASC CDL，每个 V1/V2 条目 timeline_item_color safe_set_cdl（grade.json 的 resolve 块）；"
              "slope 已验证（#13），offset/power/sat 待探针 #16。风格包有 LUT/颗粒时先套中性 DRX + drx merge [颗粒]、"
              "graph set_lut 到节点 1，再写 CDL"),
    "duck": ("音乐压低", "offline_ffmpeg", "verified", "duck.py 渲染压低版音乐床放 A2"),
    "fades": ("淡入淡出", "live_api", "verified", "set_fades，片尾淡出也用它"),
    "playback_fps": ("播放帧率", "manual", "manual", "工程设置里手动设，API 改不了"),
}


def load_json(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def video_meta(ref: Path) -> dict:
    """Whose video this is, from opencli's meta.json (field/value rows) or a kept copy of it."""
    raw = load_json(ref / "meta.json")
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        raw = raw["data"]
    if isinstance(raw, list):
        raw = {r.get("field"): r.get("value") for r in raw if isinstance(r, dict)}
    if not isinstance(raw, dict):
        return {}
    meta = {k: raw[k] for k in META_KEYS if raw.get(k)}
    if "author" in meta:  # opencli writes "name (mid: 123)"; the mid has its own field
        meta["author"] = re.sub(r"\s*\(mid:\s*\d+\)$", "", str(meta["author"]))
    return meta


def source_of(r: dict) -> dict:
    """One sources[] entry: every pack has to say where it came from. A local file has no url."""
    meta, mv = r["meta"], r["mv"]
    bvid = meta.get("bvid") or mv.get("bvid")
    src = mv.get("source") or {}
    entry = {"id": r["id"], "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
             "title": meta.get("title") or Path(src.get("path") or src.get("name") or r["id"]).name,
             "measured": mv.get("measured") or dt.date.today().isoformat()}
    if meta.get("author"):
        entry["author"] = meta["author"]
    return entry


def r3(x):
    return None if x is None else round(float(x), 3)


def dist(xs, nd: int = 3) -> dict:
    """Schema DIST, or {} when there is nothing to summarise (callers drop empty ones)."""
    s = stats.summary(xs, nd)
    return {k: s[k] for k in ("median", "mad", "p10", "p90", "n") if s.get(k) is not None} if s["n"] else {}


def events_of(mv: dict) -> list[dict]:
    return (mv.get("strips") or {}).get("events") or [dict(e, i=k) for k, e in enumerate((mv.get("scan") or {}).get("events", []))]


# ---------------------------------------------------------------- labels skeleton

def chapter_proposal(mv: dict) -> list[dict]:
    dur = mv["video"]["duration_s"]
    cuts = [c["t"] for c in mv["cuts"]["cuts"]]
    starts = [0.0]
    for tr in (mv.get("text") or {}).get("tracks", []):
        if tr["kind"] != "title":
            continue
        before = [c for c in cuts if tr["t_in"] - 3 <= c <= tr["t_in"]]
        t = before[-1] if before else tr["t_in"]
        if t - starts[-1] >= 20:
            starts.append(round(t, 3))
    ends = starts[1:] + [dur]
    return [{"name": "", "role": "chapter", "t0": a, "t1": b} for a, b in zip(starts, ends)]


def cmd_labels(ref: Path) -> int:
    mv = load_json(ref / "measure_video.json")
    if not mv:
        raise SystemExit(f"no measure_video.json in {ref.name}")
    out = ref / "labels.json"
    if out.exists():
        raise SystemExit(f"{out.name} exists; edit it, or move it to the Trash to start over")
    titles = (mv.get("strips") or {}).get("titles") or []
    moves = (mv.get("strips") or {}).get("moves") or []
    skel = {
        "ref": ref.name,
        "_how": "看 sheets/ 和 strips/ 后填写。每条标签都要指向测到的东西（事件 i、标题 track、镜头 i、章节时间段），"
                "对不上的会被拒收。看不准的 sure 填 false。type 取值：" + "/".join(schema.TRANSITIONS) + "/none（误报）。"
                "shots 里预填的是抽样的推拉镜头（strips/moves_*.jpg），move 填 " + "/".join(MOVES) +
                "：digital = 整幅等比放大、前后景没有视差（剪辑里加的推拉或变焦）；camera = 机位真的在动"
                "（无人机、车、步行），前景比背景放大得快、遮挡关系在变。",
        "chapters": chapter_proposal(mv),
        "events": [{"i": e["i"], "kind": e["kind"], "t": e["t"], "strip": e.get("strip"),
                    "candidates": e.get("candidates"), "type": None, "direction": None, "frames": None, "sure": None}
                   for e in events_of(mv) if not e["kind"].startswith("title_")],
        "titles": [{"track": t["track"], "text": t["text"], "t_in": t["t_in"], "role": None,
                    "font_class": None, "in": None, "out": None, "plate": None} for t in titles],
        "shots": [{"i": m["shot"], "sheet": m["sheet"], "type": m["type"], "zoom": m["zoom"], "move": None,
                   "sure": None} for m in moves],
        "effects": [],
        "grade": {"summary": None, "grain": None},
        "audio": {"mood": None, "sfx": []},
        "selection": {"opening": None, "reject": []},
        "notes": "",
    }
    out.write_text(json.dumps(skel, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out.name}: {len(skel['events'])} events, {len(skel['titles'])} titles, "
          f"{len(skel['shots'])} push/pull shots to judge, {len(skel['chapters'])} proposed chapters")
    return 0


# ---------------------------------------------------------------- reconcile

def reconcile(ref_id: str, mv: dict, lab: dict | None) -> tuple[dict, list[str]]:
    """Keep only labels that point at measured items; return (clean, rejected)."""
    lab = lab or {}
    rej: list[str] = []
    dur = mv["video"]["duration_s"]
    ev_ids = {e["i"] for e in events_of(mv)}
    shot_ids = {s["i"] for s in mv["scan"]["shots"]}
    tracks = {t["i"] for t in (mv.get("text") or {}).get("tracks", [])}
    chapters, last = [], 0.0
    for c in lab.get("chapters") or []:
        ok = c.get("role") in ROLES and 0 <= c.get("t0", -1) < c.get("t1", -1) <= dur + 0.5 and c["t0"] >= last - 0.5
        (chapters.append(c) if ok else rej.append(f"{ref_id} chapter {c}"))
        if ok:
            last = c["t1"]
    events = {}
    for e in lab.get("events") or []:
        if e.get("type") is None:
            continue
        if e.get("i") in ev_ids and (e["type"] in schema.TRANSITIONS or e["type"] == "none"):
            events[e["i"]] = e
        else:
            rej.append(f"{ref_id} event {e.get('i')}: {e.get('type')}")
    titles = {}
    for t in lab.get("titles") or []:
        if t.get("track") in tracks:
            titles[t["track"]] = t
        else:
            rej.append(f"{ref_id} title track {t.get('track')}")
    shots = {}
    for s in lab.get("shots") or []:
        if s.get("i") in shot_ids and s.get("move") in (None, *MOVES):
            shots[s["i"]] = s
        else:
            rej.append(f"{ref_id} shot {s.get('i')}" + (f": move {s['move']}" if s.get("move") else ""))
    effects = []
    for fx in lab.get("effects") or []:
        if fx.get("global") or fx.get("event") in ev_ids or fx.get("shot") in shot_ids:
            effects.append(fx)
        else:
            rej.append(f"{ref_id} effect {fx.get('type')} (no event/shot/global)")
    clean = {"chapters": chapters, "events": events, "titles": titles, "shots": shots, "effects": effects,
             "grade": lab.get("grade") or {}, "audio": lab.get("audio") or {}, "selection": lab.get("selection") or {}}
    return clean, rej


# ---------------------------------------------------------------- per reference

def analyse_ref(ref: Path) -> dict:
    mv = load_json(ref / "measure_video.json")
    if not mv or "scan" not in mv:
        raise SystemExit(f"{ref.name}: run measure_video.py first")
    ma = load_json(ref / "measure_audio.json", {}) or {}
    lab, rejected = reconcile(ref.name, mv, load_json(ref / "labels.json"))
    fps, dur = mv["video"]["fps"], mv["video"]["duration_s"]
    chapters = lab["chapters"] or [{"name": "all", "role": "chapter", "t0": 0.0, "t1": dur}]

    def section_of(t: float) -> int:
        for k, c in enumerate(chapters):
            if c["t0"] <= t < c["t1"]:
                return k
        return len(chapters) - 1

    shots = mv["scan"]["shots"]
    for s in shots:
        s["sec"] = section_of((s["t0"] + s["t1"]) / 2)
    cuts = mv["cuts"]["cuts"]
    by_cut = {e["cut"]: e for e in events_of(mv) if "cut" in e}
    bounds = [c["t0"] for c in chapters[1:]]
    trans, unverified = [], []
    for ci, c in enumerate(cuts):
        e = by_cut.get(ci)
        lab_e = lab["events"].get(e["i"]) if e else None
        if lab_e and lab_e["type"] != "none":
            ty, frames = lab_e["type"], lab_e.get("frames")
        elif lab_e:  # false positive: a plain cut
            ty, frames = "hard_cut", 0
        elif e and e["kind"] in MEASURED_TO_TYPE:
            ty, frames = MEASURED_TO_TYPE[e["kind"]], None
            if ty != "hard_cut":
                unverified.append(f"{ref.name} event {e['i']} {e['kind']} @ {e['t']}s")
        else:
            ty, frames = ("dissolve" if c["kind"] == "gradual" else "hard_cut"), None
        if frames is None and ty != "hard_cut":
            ev = (e or {}).get("evidence") or {}
            frames = (ev.get("flash") or {}).get("frames") if ty == "flash_white" else \
                (ev.get("dip") or {}).get("frames") if ty == "dip_to_black" else c["frames"] or None
        near = min((abs(c["t"] - b) for b in bounds), default=99)
        where = "within_section"
        if near <= 1.0:
            k = section_of(c["t"])
            prev = chapters[k - 1] if k else chapters[k]
            where = "chapter_boundary" if prev["role"] == chapters[k]["role"] == "chapter" else "section_boundary"
        trans.append({"type": ty, "frames": frames, "where": where, "t": c["t"],
                      "direction": (lab_e or {}).get("direction") or ((e or {}).get("evidence") or {}).get("whip_dir"),
                      "sec": section_of(c["t"])})
    beats = (ma.get("beats") or {})
    align = stats.beat_alignment([c["t"] for c in cuts], beats.get("beats") or [], tol=1.0 / fps,
                                 spans=beats.get("nonspeech_spans"), downbeats=beats.get("downbeats")) \
        if beats.get("beats") else {"verdict": "no_beats"}
    return {"id": ref.name, "meta": video_meta(ref), "mv": mv, "ma": ma, "lab": lab, "rejected": rejected, "unverified": unverified,
            "fps": fps, "duration": dur, "chapters": chapters, "shots": shots, "trans": trans, "align": align}


# ---------------------------------------------------------------- pooling

def groups_of(refs: list[dict], fn) -> dict:
    """{group: [values]}: one group per reference, or per chapter when there is one reference."""
    if len(refs) > 1:
        return {r["id"]: [v for s in r["shots"] for v in fn(r, s)] for r in refs}
    r = refs[0]
    out: dict = {}
    for s in r["shots"]:
        c = r["chapters"][s["sec"]]
        if c["role"] == "chapter":
            out.setdefault(f"{c.get('name') or 'chapter'}@{c['t0']:.0f}", []).extend(fn(r, s))
    return out


def look_text(look: dict) -> str:
    def m(k):
        return ((look.get(k) or {}).get("median"))
    mid_b, mid_a, lstd, l50 = m("mid_b"), m("mid_a"), m("Lstd"), m("L50")
    temp = "中性" if mid_b is None or abs(mid_b) < 2 else ("偏暖" if mid_b > 0 else "偏冷")
    tint = "" if mid_a is None or abs(mid_a) < 2 else ("偏品红" if mid_a > 0 else "偏绿")
    con = "对比中等" if lstd is None or 15 <= lstd <= 22 else ("对比低" if lstd < 15 else "对比高")
    key = "" if l50 is None else ("暗调" if l50 < 35 else "亮调" if l50 > 60 else "中间调")
    return "，".join(x for x in (key, temp + tint, con) if x)


def build_style(name: str, refs: list[dict], size: tuple[int, int]) -> dict:
    single = len(refs) == 1
    total = sum(r["duration"] for r in refs)
    all_shots = [s for r in refs for s in r["shots"]]
    all_trans = [t for r in refs for t in r["trans"]]
    evidence: dict = {"basis_groups": "chapters of one reference" if single else "references", "consistency": {}}

    def consist(key, fn, rel_tol=0.25):
        c = stats.consistency(groups_of(refs, fn), rel_tol=rel_tol)
        evidence["consistency"][key] = c
        return c["verdict"]

    # structure: role shares and per-role shot lengths
    role_time: Counter = Counter()
    for r in refs:
        for c in r["chapters"]:
            role_time[c["role"]] += c["t1"] - c["t0"]
    sections = []
    for role in ROLES:
        if not role_time[role]:
            continue
        lens = [s["len_s"] for r in refs for s in r["shots"] if r["chapters"][s["sec"]]["role"] == role]
        sec = {"role": role, "share": round(role_time[role] / total, 4), "shot_len_s": dist(lens)}
        firsts = [t["type"] for r in refs for t in r["trans"]
                  if t["where"] != "within_section" and r["chapters"][t["sec"]]["role"] == role]
        if firsts:
            top = Counter(firsts).most_common(1)[0][0]
            if top != "hard_cut":
                sec["transition_in"] = top
        sections.append(sec)
    drift = 1.0 - sum(s["share"] for s in sections)
    sections[max(range(len(sections)), key=lambda k: sections[k]["share"])]["share"] = round(
        max(sections, key=lambda s: s["share"])["share"] + drift, 4)

    lens = [s["len_s"] for s in all_shots]
    consist("shot_len_s", lambda r, s: [s["len_s"]])
    rhythm = {"shot_len_s": dist(lens), "cuts_per_min": round(len(all_trans) / (total / 60), 2),
              "min_shot_s": round(max(0.3, stats.percentile(lens, 5) or 0.3), 2)}

    # beat
    al = [r["align"] for r in refs if r["align"].get("hit_rate") is not None]
    conf = "ok"
    if not al or any(a.get("verdict") in ("too_few_cuts", "no_beats", "no_cuts_in_beat_spans") for a in [r["align"] for r in refs]) \
            or any((r["ma"].get("beats") or {}).get("confidence") == "uncertain" for r in refs):
        conf = "uncertain"
    beat = {"hit_rate": round(stats.median([a["hit_rate"] for a in al]) or 0.0, 4),
            "baseline": round(stats.median([a["baseline"] for a in al]) or 0.0, 4),
            "offset_frames": int(round((stats.median([(a.get("offset_s") or {}).get("median") or 0 for a in al]) or 0)
                                       * refs[0]["fps"])),
            "use_downbeats": bool(al and (stats.median([a.get("downbeat_hit_rate") or 0 for a in al]) or 0)
                                  >= 0.5 * (stats.median([a["hit_rate"] for a in al]) or 1)),
            "confidence": conf}
    evidence["beat"] = [{"ref": r["id"], **{k: v for k, v in r["align"].items() if k != "offset_s"}} for r in refs]

    # transitions
    tc = Counter(t["type"] for t in all_trans)
    transitions = []
    for ty, n in tc.most_common():
        ts = [t for t in all_trans if t["type"] == ty]
        item = {"type": ty, "share": round(n / len(all_trans), 4)}
        if ty != "hard_cut":
            fr = [t["frames"] for t in ts if t["frames"]]
            item["frames"] = int(round(stats.median(fr))) if fr else DEFAULT_FRAMES.get(ty, 10)
            wc = Counter(t["where"] for t in ts)
            w, k = wc.most_common(1)[0]
            item["where"] = w if w != "within_section" and k / len(ts) >= 0.6 else "any"
            dirs = Counter(t["direction"] for t in ts if t["direction"])
            if dirs:
                item["direction"] = dirs.most_common(1)[0][0]
        transitions.append(item)
    if transitions:
        transitions[0]["share"] = round(transitions[0]["share"] + 1.0 - sum(t["share"] for t in transitions), 4)
    else:
        transitions = [{"type": "hard_cut", "share": 1.0}]
    evidence["consistency"]["transitions"] = {
        g: dict(Counter(t["type"] for t in ts)) for g, ts in
        ({r["id"]: r["trans"] for r in refs} if not single else
         {f"{c.get('name') or 'chapter'}@{c['t0']:.0f}": [t for t in refs[0]["trans"] if t["sec"] == k]
          for k, c in enumerate(refs[0]["chapters"])}).items()}

    # motion
    known = [s for s in all_shots if s["motion"]["type"] != "unknown"]
    nk = max(1, len(known))

    def share(types):
        return round(sum(s["motion"]["type"] in types for s in known) / nk, 4)
    # a measured push/pull is often the camera moving (drone, car, walking); plan_cut adds digital
    # pushes, so the share counts only what the `move` labels call digital, scaled to all measured ones
    moves = {(r["id"], i): sl["move"] for r in refs for i, sl in r["lab"]["shots"].items() if sl.get("move")}
    motion: dict = {}
    evidence["moves"] = {}
    for k in ("push_in", "pull_out"):
        hits = [(r["id"], s) for r in refs for s in r["shots"] if s["motion"]["type"] == k]
        judged = [(s, moves[(rid, s["i"])]) for rid, s in hits if (rid, s["i"]) in moves]
        digital = [s for s, m in judged if m == "digital"]
        if judged:
            frac, zooms = len(digital) / len(judged), [s["motion"]["zoom"] for s in digital]
        else:
            frac, zooms = 1.0, [s["motion"]["zoom"] for _, s in hits]
            if hits:
                evidence.setdefault("unverified", []).append(
                    f"{k}: {len(hits)} 个测到的推拉没判 digital/camera，比例按全部算，多半偏高")
        motion[k] = {"share": round(len(hits) / nk * frac, 4)}
        if dist(zooms):
            motion[k]["scale_end"] = dist(zooms)
        evidence["moves"][k] = {"measured": len(hits), "judged": len(judged), "digital": len(digital)}
    motion["shake"] = {"share": share({"handheld"}),
                       "amplitude_px": round((stats.median([s["motion"]["shake"] for s in known if s["motion"]["shake"] is not None]) or 0)
                                             * size[0] / 100, 1)}
    # one share per group (a median of 0/1 values is always 0 or 1)
    pushes = groups_of(refs, lambda r, s: [1.0 if s["motion"]["type"] == "push_in" else 0.0])
    evidence["consistency"]["measured_push_in_share"] = stats.consistency(
        {g: [sum(v) / len(v)] for g, v in pushes.items() if len(v) >= 2}, rel_tol=0.5, min_n=1)
    labelled = [sl for r in refs for sl in r["lab"]["shots"].values()]
    slow = [sl for sl in labelled if "slow_mo" in sl]
    if slow:
        motion["slow_mo"] = {"share": round(sum(bool(sl["slow_mo"]) for sl in slow) / len(slow), 4),
                             "speed": float(stats.median([sl.get("speed", 0.5) for sl in slow if sl["slow_mo"]]) or 0.5)}
    ramps_ok = sum(1 for r in refs for e in r["lab"]["events"].values() if e.get("ramp"))
    motion["ramp"] = {"share": round(ramps_ok / max(1, len(all_shots)), 4)}
    evidence["ramp_candidates"] = sum(1 for s in all_shots if s.get("ramp_candidate"))

    # grade
    looks = [r["mv"].get("look", {}).get("summary") or {} for r in refs]
    look = {}
    for k in ("L01", "L50", "L99", "Lstd", "shadow", "chroma", "sh_a", "sh_b", "mid_a", "mid_b", "hi_a", "hi_b", "vignette"):
        vals = [lk[k]["median"] for lk in looks if lk.get(k)]
        mads = [lk[k]["mad"] for lk in looks if lk.get(k)]
        if vals:
            look[k] = {"median": round(stats.median(vals), 3), "mad": round(stats.median(mads), 3)}
    for key in ("L50", "Lstd", "mid_b", "chroma"):
        if single:
            per = {p["shot"]: p for p in refs[0]["mv"].get("look", {}).get("per_shot", [])}
            consist(f"look.{key}", lambda r, s, key=key: [per[s["i"]][key]] if s["i"] in per and per[s["i"]].get(key) is not None else [], 0.3)
        else:
            consist(f"look.{key}", lambda r, s, key=key: [p[key] for p in r["mv"].get("look", {}).get("per_shot", [])
                                                         if p["shot"] == s["i"] and p.get(key) is not None], 0.3)
    hues = [r["mv"].get("look", {}).get("hue_hist") for r in refs if r["mv"].get("look", {}).get("hue_hist")]
    if hues:
        look["hue_hist"] = [round(stats.median([h[i] for h in hues]), 4) for i in range(12)]
    lab_grade = [r["lab"]["grade"] for r in refs]
    summ = next((g.get("summary") for g in lab_grade if g.get("summary")), None) or look_text(look)
    grain = next((g.get("grain") for g in lab_grade if g.get("grain")), None)
    grade = {"summary": summ, "look": look, "lut": None, "drx": None,
             "grain": {"strength": grain} if grain and grain != "none" else None}

    # text
    text = {}
    titles = []
    for r in refs:
        sec_t0 = [c["t0"] for c in r["chapters"]]
        for t in (r["mv"].get("strips") or {}).get("titles") or []:
            lt = r["lab"]["titles"].get(t["track"], {})
            role = lt.get("role") or "location"
            if role == "other":
                continue
            k = max(0, max((i for i, x in enumerate(sec_t0) if x <= t["t_in"] + 1e-6), default=0))
            titles.append({**t, "role": role, "lab": lt, "offset": t["t_in"] - sec_t0[k], "sec": (r["id"], k),
                           "in_chapter": r["chapters"][k]["role"] == "chapter"})
    for key, roles in (("title", {"location", "title"}), ("chapter_card", {"chapter_card"})):
        ts = [t for t in titles if t["role"] in roles]
        if not ts:
            continue
        fonts = Counter(t["lab"].get("font_class") for t in ts if t["lab"].get("font_class"))
        fclass = fonts.most_common(1)[0][0] if fonts else "黑体"
        col = [t["colour"] for t in ts if t.get("colour")]
        fill = Counter(c["fill"] for c in col).most_common(1)[0][0] if col else "#ffffff"
        # frequency and timing come from chapter sections only: an episode card at the end of the
        # intro or the cards of the ending say nothing about how a chapter opens
        n_chapters = sum(1 for r in refs for c in r["chapters"] if c["role"] == "chapter")
        in_ch = [t for t in ts if t["in_chapter"]]
        secs_with = len({t["sec"] for t in in_ch})
        spec = {"font_class": fclass, "fallback_fonts": FONT_FALLBACK.get(fclass, ["PingFang SC"]),
                "height_frac": round(stats.median([t["height"] for t in ts]), 4),
                "position": [round(stats.median([t["center"][0] for t in ts]), 3), round(stats.median([t["center"][1] for t in ts]), 3)],
                "color": [round(int(fill[i:i + 2], 16) / 255, 3) for i in (1, 3, 5)],
                "stroke": Counter(c["edge"] != "none" for c in col).most_common(1)[0][0] if col else False,
                "plate": bool(Counter(bool(t["lab"].get("plate")) for t in ts).most_common(1)[0][0]),
                "in": Counter(t["lab"].get("in") or "fade" for t in ts).most_common(1)[0][0],
                "out": Counter(t["lab"].get("out") or "fade" for t in ts).most_common(1)[0][0],
                "duration_s": round(stats.median([t["t_out"] - t["t_in"] for t in ts]), 2),
                "offset_s": round(max(0.0, stats.median([t["offset"] for t in (in_ch or ts)])), 2),
                "template": "Text+",
                "per": "chapter" if secs_with >= 0.6 * max(1, n_chapters) and len(in_ch) <= 1.5 * max(1, n_chapters)
                       else "location"}
        if not fonts:
            evidence.setdefault("unverified", []).append(f"{key}: font_class 未标注，暂填黑体")
        text[key] = spec
    bands = [(r["mv"].get("text") or {}).get("subtitle_band") for r in refs]
    bands = [b for b in bands if b]
    if bands:
        text["subtitle"] = {"height_frac": round(stats.median([b["height"] for b in bands]), 4),
                            "position": [0.5, round(stats.median([b["y"] for b in bands]), 3)], "font_class": "黑体",
                            "fallback_fonts": FONT_FALLBACK["黑体"], "stroke": True}

    # audio
    bpms = [(r["ma"].get("beats") or {}).get("bpm") for r in refs]
    audio = {"bpm": dist([b for b in bpms if b], 1),
             "mood": next((r["lab"]["audio"].get("mood") for r in refs if r["lab"]["audio"].get("mood")), "") or "",
             "lufs": stats.median([(r["ma"].get("loudness") or {}).get("integrated_lufs") for r in refs
                                   if (r["ma"].get("loudness") or {}).get("integrated_lufs") is not None]),
             "duck_db": stats.median([(r["ma"].get("duck") or {}).get("db") for r in refs
                                      if (r["ma"].get("duck") or {}).get("db") is not None]),
             "sfx": [x for r in refs for x in r["lab"]["audio"].get("sfx") or []]}
    changes = sum(len((r["ma"].get("music") or {}).get("tempo_changes") or []) + len((r["ma"].get("music") or {}).get("beat_gaps") or [])
                  for r in refs)
    n_ch = sum(1 for r in refs for c in r["chapters"] if c["role"] == "chapter")
    audio["music_changes"] = f"约每章 {changes / max(1, n_ch):.1f} 处换曲/停顿候选（tempo 变化 + 拍点中断），按章节换曲需人工确认"
    for k in ("bpm", "lufs", "duck_db"):
        if audio[k] is None or audio[k] == {}:
            del audio[k]
        elif not isinstance(audio[k], dict):
            audio[k] = round(audio[k], 1)
    onsets = [(r["ma"].get("onsets") or {}).get("per_min") for r in refs]
    evidence["onsets_per_min"] = stats.median([o for o in onsets if o is not None])

    # selection
    types = Counter(sl.get("shot_type") for sl in labelled if sl.get("shot_type"))
    sel = {"max_shake": round((stats.percentile([s["motion"]["shake"] for s in known if s["motion"]["shake"] is not None], 90) or 0.5) * 1.2, 3)}
    if types:
        sel["shot_types"] = {k: round(v / sum(types.values()), 3) for k, v in types.most_common()}
    for r in refs:
        s = r["lab"]["selection"]
        if s.get("opening"):
            sel["opening"] = s["opening"]
        sel.setdefault("reject", []).extend(s.get("reject") or [])

    # resolve checklist for the elements this style uses
    used = {t["type"] for t in transitions} | {"grade", "fades", "playback_fps", "hard_cut"}
    if motion["push_in"]["share"] or motion["pull_out"]["share"]:
        used.add("push_in")
    if (motion.get("slow_mo") or {}).get("share"):
        used.add("slow_mo")
    if motion["ramp"]["share"]:
        used.add("ramp")
    if text.get("title") or text.get("chapter_card"):
        used.add("title")
    if text.get("subtitle"):
        used.add("subtitle")
    if audio.get("duck_db"):
        used.add("duck")
    lb = [r["mv"]["scan"]["letterbox"]["aspect"] for r in refs if r["mv"]["scan"]["letterbox"]["aspect"]]
    if lb:
        used.add("letterbox")
    resolve = [{"element": ROUTES[k][0], "route": ROUTES[k][1], "status": ROUTES[k][2], "how": ROUTES[k][3]}
               for k in ROUTES if k in used]
    effects = [{"type": fx["type"], **{k: v for k, v in fx.items() if k not in ("type",)}} for r in refs for fx in r["lab"]["effects"]]

    src_fps = Counter(round(float(r["mv"]["video"]["source_fps"]), 3) for r in refs).most_common(1)[0][0]
    style = {
        "schema_version": 1, "name": name, "summary": "",
        "sources": [source_of(r) for r in refs],
        "basis": "single_reference_chapters" if single else "multi_reference",
        "deliverable": {"fps": src_fps, "width": size[0], "height": size[1],
                        "duration_s": dist([r["duration"] for r in refs], 1),
                        "letterbox": round(stats.median(lb), 2) if lb else None},
        "structure": {"sections": sections},
        "selection": sel,
        "rhythm": rhythm,
        "beat": beat,
        "transitions": transitions,
        "motion": motion,
        "effects": effects,
        "grade": grade,
        "text": text,
        "audio": audio,
        "resolve": resolve,
        "evidence": evidence,
    }
    evidence["unverified"] = evidence.get("unverified", []) + [u for r in refs for u in r["unverified"]]
    evidence["rejected_labels"] = [x for r in refs for x in r["rejected"]]
    evidence["single_source"] = sorted(k for k, v in evidence["consistency"].items()
                                       if isinstance(v, dict) and v.get("verdict") == "single_source")
    return style


def cmd_draft(args) -> int:
    out = Path(args.out).expanduser() if args.out else Path(args.refs[0]).expanduser() / "style.draft.json"
    if args.out and out.exists() and not args.force:
        raise SystemExit(f"{out.name} exists and may hold hand-written parts: leave out --out to write "
                         "REF_DIR/style.draft.json and merge by hand, or add --force")
    refs = [analyse_ref(Path(d).expanduser().resolve()) for d in args.refs]
    mids = sorted({str(r["meta"]["mid"]) for r in refs if r["meta"].get("mid")})
    if len(mids) > 1:
        print(f"warning: references come from {len(mids)} uploaders (mid {', '.join(mids)}); "
              "a creator album should pool one", file=sys.stderr)
    w, h = (int(x) for x in args.size.lower().split("x"))
    style = build_style(args.name, refs, (w, h))
    errs = schema.check_style(style)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")
    ev = style["evidence"]
    print(f"wrote {out.name}: {len(style['structure']['sections'])} sections, "
          f"{len(style['transitions'])} transition types, beat {style['beat']['hit_rate']} vs {style['beat']['baseline']}; "
          f"{len(ev['unverified'])} unverified, {len(ev['rejected_labels'])} rejected labels")
    for e in errs:
        print(f"schema: {e}", file=sys.stderr)
    return 1 if errs else 0


# ---------------------------------------------------------------- footage

NEW_DAY_GAP_S = 5 * 3600  # a break this long between clips starts a new shooting day
MOTION_KIND = {"pan_left": "pan", "pan_right": "pan", "tilt_up": "tilt", "tilt_down": "tilt"}


def epoch(iso: str) -> float:
    t = dt.datetime.fromisoformat(iso)
    return t.replace(tzinfo=t.tzinfo or dt.timezone.utc).timestamp()


def clip_time(prep: dict, tagged: dict, offsets: dict) -> dict:
    """shot_at after the clock fixes. A time typed into tags.json is the truth and takes no offset."""
    device = prep.get("device") or prep["probe"].get("device") or "unknown"
    if tagged.get("shot_at"):
        iso = dt.datetime.fromtimestamp(epoch(tagged["shot_at"]), dt.timezone.utc).isoformat()
        return {"shot_at": iso, "shot_at_source": "tags", "device": device}
    iso = prep["probe"].get("shot_at")
    if iso and offsets.get(device):
        iso = dt.datetime.fromtimestamp(epoch(iso) + float(offsets[device]), dt.timezone.utc).isoformat()
    return {"shot_at": iso, "shot_at_source": prep["probe"].get("shot_at_source") if iso else "filename",
            "device": device}


def order_clips(clips: list[dict]) -> None:
    """Global `order` and shooting `day`, written as fields: the clips array keeps its own order.

    Days come from long gaps, not calendar dates: a time tag may be local or UTC and
    a trip crosses zones, while nobody films through the night. Clips with no time
    follow the dated ones in file-name order and belong to no day. Photos take part
    as instants.
    """
    day, last_end = 1, None
    for n, c in enumerate(sorted(clips, key=lambda c: (c["shot_at"] is None, epoch(c["shot_at"]) if c["shot_at"] else 0,
                                                       c["source"]))):
        c["order"], c["day"] = n, None
        if c["shot_at"]:
            t = epoch(c["shot_at"])
            if last_end is not None and t - last_end >= NEW_DAY_GAP_S:
                day += 1
            c["day"] = day
            last_end = max(last_end or t, t + (c["frames"] / c["fps"] if c.get("fps") else 0.0))


def cmd_footage(project: str) -> int:
    P = vpaths.load()
    proj = P.project(project)
    tags = load_json(proj / "index" / "tags.json", {}) or {}
    offsets = (load_json(proj / "plan" / "brief.json", {}) or {}).get("clock_offsets_s") or {}
    clips, photos, sharp_all = [], [], []
    for d in sorted((proj / "proxies").iterdir()) if (proj / "proxies").exists() else []:
        prep, mv = load_json(d / "prep.json"), load_json(d / "measure_video.json")
        if prep and prep.get("kind") == "photo":
            ct = (tags.get("clips") or {}).get(d.name, {})
            photos.append({"id": d.name, "kind": "photo", "source": prep["source"]["path"],
                           "width": prep["probe"]["width"], "height": prep["probe"]["height"],
                           "preview": P.rel(d / prep["preview"]), **clip_time(prep, ct, offsets),
                           "shot_type": ct.get("shot_type"), "tags": ct.get("tags") or [],
                           **({"reject": True} if ct.get("reject") else {}),
                           **({"movable": True} if ct.get("movable") else {}),
                           **({"light": ct["light"]} if ct.get("light") else {})})
            continue
        if not prep or not mv or "scan" not in mv:
            print(f"skip {d.name}: needs prep.json and measure_video.json", file=sys.stderr)
            continue
        ma = load_json(d / "measure_audio.json", {}) or {}
        fps = float(prep["probe"]["fps"])
        if prep["probe"].get("vfr"):
            print(f"warn {d.name}: variable frame rate source; proxy frames may not match source frames", file=sys.stderr)
        speech = [[round(a * fps), round(b * fps)] for a, b in ma.get("speech") or []]
        # a 4th element names the language of a line that is not Chinese (measure_audio --language auto)
        sentences = [[round(r["t_in"] * fps), round(r["t_out"] * fps), r["text"],
                      *([r["lang"]] if r.get("lang") not in (None, "zh") else [])]
                     for r in (ma.get("transcript") or {}).get("segments") or [] if r.get("text")]
        ct = (tags.get("clips") or {}).get(d.name, {})
        shots = []
        for s in mv["scan"]["shots"]:
            st = (ct.get("shots") or {}).get(str(s["i"]), {})
            shots.append({"start": s["start"], "end": s["end"],
                          "shot_type": st.get("shot_type") or ct.get("shot_type"),
                          "tags": st.get("tags") or ct.get("tags") or [],
                          "motion": MOTION_KIND.get(s["motion"]["type"], s["motion"]["type"]),
                          "sharp_raw": s["sharp_raw"], "exposure_ok": s["exposure_ok"],
                          "shake": s["motion"]["shake"] or 0.0, "best": s["best"],
                          "speech": [[max(a, s["start"]), min(b, s["end"])] for a, b in speech if b > s["start"] and a < s["end"]],
                          "sentences": [[max(a, s["start"]), min(b, s["end"]), *rest]
                                        for a, b, *rest in sentences if b > s["start"] and a < s["end"]],
                          **({"reject": True} if st.get("reject") or ct.get("reject") else {}),
                          **({"movable": True} if st.get("movable", ct.get("movable")) else {}),
                          **({"light": st.get("light") or ct.get("light")} if st.get("light") or ct.get("light") else {})})
            sharp_all.append(s["sharp_raw"] or 0.0)
        clips.append({"id": d.name, "source": prep["source"]["path"], "fps": fps, "frames": mv["cuts"]["n_frames"],
                      "width": prep["probe"]["width"], "height": prep["probe"]["height"],
                      "hdr": prep["probe"]["hdr"], **clip_time(prep, ct, offsets), "sentences": sentences,
                      "shots": shots})
    order_clips(clips + photos)
    order = sorted(sharp_all)
    for c in clips:
        for s in c["shots"]:
            raw = s.pop("sharp_raw") or 0.0
            s["sharp"] = round(sum(1 for v in order if v <= raw) / max(1, len(order)), 3)
    out = proj / "index" / "footage.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"clips": clips, **({"photos": photos} if photos else {})}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    untagged = sum(1 for c in clips for s in c["shots"] if not s["tags"]) + sum(1 for x in photos if not x["tags"])
    print(f"wrote {P.rel(out)}: {len(clips)} clips, {sum(len(c['shots']) for c in clips)} shots"
          + (f", {len(photos)} photos" if photos else "") + f", {untagged} untagged")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lab = sub.add_parser("labels")
    lab.add_argument("ref", type=Path)
    dr = sub.add_parser("draft")
    dr.add_argument("refs", nargs="+")
    dr.add_argument("--name", required=True)
    dr.add_argument("--out")
    dr.add_argument("--size", default="3840x2160")
    dr.add_argument("--force", action="store_true", help="overwrite an existing --out")
    ft = sub.add_parser("footage")
    ft.add_argument("--project", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "labels":
        return cmd_labels(args.ref.expanduser().resolve())
    if args.cmd == "draft":
        return cmd_draft(args)
    return cmd_footage(args.project)


if __name__ == "__main__":
    sys.exit(main())

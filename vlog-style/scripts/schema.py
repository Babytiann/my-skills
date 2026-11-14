#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style.json / cut_plan.json schemas and a small stdlib validator.

The validator covers the JSON-Schema subset these files use: type, required,
properties, additionalProperties(false), items, enum, minimum, maximum,
minItems. references/style-schema.md documents every field in prose; this file
is the machine contract plan_cut.py and compare.py rely on.

Usage: python3 -I -B schema.py style|plan FILE.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TRANSITIONS = ["hard_cut", "dissolve", "dip_to_black", "flash_white", "whip", "zoom",
               "smooth_cut", "wipe", "other"]
DIRECTED = ["whip", "wipe"]  # the only transitions that move one way; the rest have no direction
CONSISTENCY = ["consistent", "varies", "single_source", "uncertain"]
ROUTES = ["live_api", "fusion", "offline_drt", "offline_drx", "offline_ffmpeg", "manual"]
STATUSES = ["verified", "probe", "manual"]

NUM = {"type": "number"}
DIST = {  # robust summary of one quantity
    "type": "object",
    "required": ["median"],
    "properties": {"median": NUM, "mad": NUM, "p10": NUM, "p90": NUM, "n": {"type": "integer"}},
}
SHARE = {"type": "number", "minimum": 0, "maximum": 1}
TEXT_STYLE = {
    "type": "object",
    "properties": {
        "font_class": {"type": "string"},
        "fallback_fonts": {"type": "array", "items": {"type": "string"}},
        "height_frac": NUM,
        "position": {"type": "array", "items": NUM},
        "color": {"type": "array", "items": NUM},
        "stroke": {"type": "boolean"},
        "plate": {"type": "boolean"},
        "in": {"type": "string"},
        "out": {"type": "string"},
        "duration_s": NUM,
        "offset_s": NUM,
        "template": {"type": "string"},
        "per": {"type": "string", "enum": ["chapter", "location", "section", "never"]},
    },
}

MUSIC = {  # optional: packs distilled before measure_music.py have none (a warning, not an error)
    "type": "object",
    "required": ["refs"],
    "properties": {
        "refs": {"type": "integer", "minimum": 1},
        "coverage": DIST, "songs_per_10min": DIST, "song_s": DIST, "bpm": DIST, "bed_s": DIST,
        "enter_s": DIST, "body_s": DIST, "fade_in_s": DIST, "fade_out_s": DIST,
        "under_speech": DIST, "duck_db": DIST,
        "vocal": {"type": "object", "required": ["share", "verdict"], "properties": {
            "share": SHARE, "verdict": {"type": "string", "enum": ["instrumental", "vocal", "mixed"]}}},
        "change_at": {"type": "string", "enum": ["chapter", "within_chapter", "none"]},
        "tags": {"type": "array", "items": {"type": "object", "required": ["cat", "tag", "zh", "share"], "properties": {
            "cat": {"type": "string", "enum": ["genre", "mood", "instrument", "energy"]}, "tag": {"type": "string"},
            "zh": {"type": "string"}, "share": SHARE}}},
        "energy_curve": {"type": "array", "items": NUM},
    },
}

STYLE = {
    "type": "object",
    "required": ["schema_version", "name", "sources", "basis", "deliverable", "structure",
                 "rhythm", "beat", "transitions", "motion", "grade", "text", "audio", "resolve"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "name": {"type": "string"},
        "summary": {"type": "string"},
        "sources": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["id", "url", "title", "measured"],
            "properties": {"id": {"type": "string"}, "url": {"type": "string"},  # empty only for a local file
                           "measured": {"type": "string"}, "title": {"type": "string"},
                           "author": {"type": "string"}}}},
        "basis": {"type": "string", "enum": ["multi_reference", "single_reference_chapters"]},
        "deliverable": {"type": "object", "required": ["fps", "width", "height"], "properties": {
            "fps": {"type": "number", "minimum": 1}, "width": {"type": "integer", "minimum": 16},
            "height": {"type": "integer", "minimum": 16}, "duration_s": DIST,
            "letterbox": {"type": ["number", "null"]}}},
        "structure": {"type": "object", "required": ["sections"], "properties": {
            "order": {"type": "string", "enum": ["chronological", "free"]},
            "order_tolerance_s": {"type": "number", "minimum": 0},
            "sections": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["role", "share", "shot_len_s"],
                "properties": {
                    "role": {"type": "string", "enum": ["hook", "intro", "chapter", "interlude", "ending"]},
                    "share": SHARE,
                    "shot_len_s": DIST,
                    "transition_in": {"type": "string", "enum": TRANSITIONS},
                    "notes": {"type": "string"}}}}}},
        "selection": {"type": "object", "properties": {
            "shot_types": {"type": "object"},
            "opening": {"type": "string"},
            "reject": {"type": "array", "items": {"type": "string"}},
            "max_shake": NUM}},
        "rhythm": {"type": "object", "required": ["shot_len_s"], "properties": {
            "shot_len_s": DIST, "cuts_per_min": NUM, "min_shot_s": NUM}},
        "beat": {"type": "object", "required": ["hit_rate"], "properties": {
            "hit_rate": SHARE, "baseline": SHARE, "offset_frames": {"type": "integer"},
            "use_downbeats": {"type": "boolean"}, "confidence": {"type": "string", "enum": CONSISTENCY + ["ok"]}}},
        "transitions": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["type", "share"],
            "properties": {"type": {"type": "string", "enum": TRANSITIONS}, "share": SHARE,
                           "frames": {"type": "integer", "minimum": 0},
                           "where": {"type": "string", "enum": ["any", "within_section", "section_boundary", "chapter_boundary"]},
                           "direction": {"type": "string"}}}},
        "motion": {"type": "object", "properties": {
            "push_in": {"type": "object", "properties": {"share": SHARE, "scale_end": DIST}},
            "pull_out": {"type": "object", "properties": {"share": SHARE, "scale_end": DIST}},
            "slow_mo": {"type": "object", "properties": {"share": SHARE, "speed": {"type": "number", "minimum": 0.05, "maximum": 1}}},
            "ramp": {"type": "object", "properties": {"share": SHARE}},
            "shake": {"type": "object", "properties": {"share": SHARE, "amplitude_px": NUM}}}},
        "effects": {"type": "array", "items": {"type": "object", "required": ["type"]}},
        "grade": {"type": "object", "required": ["look"], "properties": {
            "summary": {"type": "string"}, "look": {"type": "object"},
            "lut": {"type": ["string", "null"]}, "drx": {"type": ["string", "null"]},
            "grain": {"type": ["object", "null"]}}},
        "text": {"type": "object", "properties": {
            "title": TEXT_STYLE, "chapter_card": TEXT_STYLE, "subtitle": TEXT_STYLE}},
        "music": MUSIC,
        "audio": {"type": "object", "properties": {
            "bpm": DIST, "mood": {"type": "string"}, "lufs": NUM, "duck_db": NUM,
            "music_changes": {"type": "string"}, "sfx": {"type": "array"},
            "broll_ambient_db": {"type": ["number", "null"]}}},
        "aroll": {"type": "object", "properties": {
            "speech_share": SHARE, "pad_s": {"type": "number", "minimum": 0}, "pad_out_s": {"type": "number", "minimum": 0},
            "cutaway_after_s": {"type": "number", "minimum": 0}, "cutaway_share": SHARE,
            "cutaway_lead_s": {"type": "number", "minimum": 0}, "cutaway_tail_s": {"type": "number", "minimum": 0},
            "j_s": {"type": "number", "minimum": 0}, "l_s": {"type": "number", "minimum": 0}}},
        "photo": {"type": "object", "properties": {
            "dur_s": {"type": "number", "minimum": 0.2}, "scale": {"type": "number", "minimum": 0.1, "maximum": 1},
            "background": {"type": "string", "enum": ["black"]}}},
        "resolve": {"type": "array", "items": {
            "type": "object", "required": ["element", "route", "status"],
            "properties": {"element": {"type": "string"}, "route": {"type": "string", "enum": ROUTES},
                           "status": {"type": "string", "enum": STATUSES}, "how": {"type": "string"}}}},
        "evidence": {"type": "object"},
    },
}

PLAN_CLIP = {
    "type": "object",
    "required": ["id", "section", "source", "source_fps", "src_in", "src_out", "rec_in", "rec_out", "speed", "track"],
    "properties": {
        "id": {"type": "string"}, "section": {"type": "string"}, "source": {"type": "string"},
        "source_fps": {"type": "number", "minimum": 1},
        "src_in": {"type": "integer", "minimum": 0}, "src_out": {"type": "integer", "minimum": 1},
        "rec_in": {"type": "integer", "minimum": 0}, "rec_out": {"type": "integer", "minimum": 1},
        "speed": {"type": "number", "minimum": 0.05, "maximum": 4}, "track": {"type": "integer", "minimum": 1},
        "transition_out": {"type": ["object", "null"]}, "motion": {"type": ["object", "null"]},
        "role": {"type": "string", "enum": ["aroll", "broll", "cutaway", "photo"]},
        "kind": {"type": "string", "enum": ["photo"]}, "photo": {"type": "string"},
        "scale": {"type": "number", "minimum": 0.1, "maximum": 1},
    },
}
PLAN_DIALOGUE = {
    "type": "object",
    "required": ["id", "clip", "source", "source_fps", "src_in", "src_out", "rec_in", "rec_out", "track", "sentences"],
    "properties": {
        "src_in": {"type": "integer", "minimum": 0}, "src_out": {"type": "integer", "minimum": 1},
        "rec_in": {"type": "integer", "minimum": 0}, "rec_out": {"type": "integer", "minimum": 1},
        "source_fps": {"type": "number", "minimum": 1}, "track": {"type": "integer", "minimum": 1},
        "sentences": {"type": "array", "minItems": 1},
    },
}
PLAN = {
    "type": "object",
    "required": ["schema_version", "style", "seed", "timeline", "video", "titles", "audio", "markers"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "timeline": {"type": "object", "required": ["fps", "width", "height", "duration_frames"]},
        "video": {"type": "array", "items": PLAN_CLIP},
        "titles": {"type": "array"}, "audio": {"type": "array"}, "markers": {"type": "array"},
        "dialogue": {"type": "array", "items": PLAN_DIALOGUE},
    },
}

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value
    return isinstance(value, _TYPES[t])


def validate(value, schema: dict, where: str = "$") -> list[str]:
    errs: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is(value, x) for x in types):
            return [f"{where}: expected {'/'.join(types)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{where}: {value!r} not in {schema['enum']}")
    if _is(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{where}: {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{where}: {value} > {schema['maximum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{where}: missing {k}")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], f"{where}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{where}: unexpected {k}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{where}: needs at least {schema['minItems']} item(s)")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += validate(v, schema["items"], f"{where}[{i}]")
    return errs


def check_style(style: dict) -> list[str]:
    errs = validate(style, STYLE)
    if errs:
        return errs
    for i, src in enumerate(style["sources"]):
        if not src["title"].strip():
            errs.append(f"$.sources[{i}].title: empty, every pack must say where it came from")
    shares = sum(s["share"] for s in style["structure"]["sections"])
    if abs(shares - 1.0) > 0.02:
        errs.append(f"$.structure.sections: shares sum to {shares:.3f}, expected 1.0")
    tshare = sum(t["share"] for t in style["transitions"])
    if abs(tshare - 1.0) > 0.02:
        errs.append(f"$.transitions: shares sum to {tshare:.3f}, expected 1.0")
    for i, t in enumerate(style["transitions"]):
        if "direction" in t and t["type"] not in DIRECTED:
            errs.append(f"$.transitions[{i}].direction: a {t['type']} has no direction "
                        f"(only {'/'.join(DIRECTED)} do); leave the key out")
    return errs


def style_warnings(style: dict) -> list[str]:
    if "music" not in style:
        return ["no music block: run measure_music.py on the references, then aggregate.py draft --section music"]
    return []


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ("style", "plan"):
        print(__doc__, file=sys.stderr)
        return 2
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    errs = check_style(data) if argv[0] == "style" else validate(data, PLAN)
    for e in errs:
        print(e)
    for w in style_warnings(data) if argv[0] == "style" else []:
        print(f"warning: {w}", file=sys.stderr)
    print("ok" if not errs else f"{len(errs)} problem(s)", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())

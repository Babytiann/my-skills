#!/usr/bin/env python3
"""Build the Links style album review page.

  python3 showcase/links-review/build.py

Reads only STYLE.md and style.json under vlog-style/styles/links (the merged pack and three
sub-packs), never stills/ or refs/, and writes index.html next to this file. Exits non-zero
if a number on the page does not match its source.
"""
import html
import json
import re
import statistics
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LINKS = ROOT / "vlog-style/styles/links"
REL = "../../vlog-style/styles/links"

VIDEOS = (("island", "孤岛", "lonely-house"), ("kula", "库拉岗日", "kula-kangri"), ("snow", "雪山", "first-snow-mountain"))
DIRS = {"merged": LINKS, **{k: LINKS / d for k, _, d in VIDEOS}}
MD = {k: (p / "STYLE.md").read_text(encoding="utf-8") for k, p in DIRS.items()}
J = {k: json.loads((p / "style.json").read_text(encoding="utf-8")) for k, p in DIRS.items()}
BV = {k: J[k]["sources"][0]["id"] for k, _, _ in VIDEOS}
PREFIX = {"merged": "links", "snow": "snow"}

FAILS: list[str] = []
COUNT = {"numbers": 0, "quotes": 0, "lines": 0}


# ---------------------------------------------------------------- formatting

def esc(s) -> str:
    return html.escape(str(s), quote=False)


def attr(s) -> str:
    return html.escape(str(s), quote=True).replace("\n", "&#10;")


def fx(x, d=1) -> str:
    v = Decimal(str(x)).quantize(Decimal(1).scaleb(-d), ROUND_HALF_UP)
    return f"{v}".replace("-", "−")


def pct(x, d=1) -> str:
    return fx(Decimal(str(x)) * 100, d) + "%"


def signed(x, d=1) -> str:
    s = fx(x, d)
    return s if s.startswith("−") else "+" + s


def hexcolor(rgb) -> str:
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in rgb)


def inline(s: str) -> str:
    codes: list[str] = []

    def stash(m):
        codes.append(f"<code>{esc(m[1])}</code>")
        return f"\0{len(codes) - 1}\0"

    s = esc(re.sub(r"`([^`]+)`", stash, s))
    tags = {"一致": "ok", "不一致": "diff", "单一来源": "one", "存疑": "doubt"}

    def bold(m):
        return tag(m[1], tags[m[1]]) if m[1] in tags else f"<strong>{m[1]}</strong>"

    s = re.sub(r"\*\*(.+?)\*\*", bold, s)
    return re.sub(r"\0(\d+)\0", lambda m: codes[int(m[1])], s)


def tag(text: str, kind: str) -> str:
    return f'<span class="tag tag-{kind}">{esc(text)}</span>'


# ---------------------------------------------------------------- reading the sources

def sections(md: str) -> dict:
    out, key = {"pre": []}, "pre"
    for line in md.splitlines():
        m = re.match(r"## (\d+)\. ", line)
        if m:
            key = int(m[1])
            out[key] = []
        elif line.startswith("## 炼化记录"):
            key = "log"
            out[key] = []
        out[key].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


SEC = {k: sections(v) for k, v in MD.items()}


def md_tables(text: str) -> list[list[list[str]]]:
    tables, cur = [], []
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                cur.append(cells)
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return tables


def row(sec, name: str, doc="merged") -> list[str]:
    for t in md_tables(SEC[doc][sec]):
        for r in t:
            if r[0] == name:
                return r
    sys.exit(f"{doc} §{sec}: no table row named {name!r}")


NUM = re.compile(r"[-−+]?\d+(?:\.\d+)?")


def same(label: str, cell: str, value, nth=0, scale=1) -> None:
    """The number written in a STYLE.md cell must round from the style.json value."""
    COUNT["numbers"] += 1
    found = NUM.findall(cell)
    if len(found) <= nth:
        FAILS.append(f"{label}: no number #{nth} in {cell!r}")
        return
    text = found[nth]
    decimals = len(text.split(".")[1]) if "." in text else 0
    if abs(float(text.replace("−", "-")) - float(value) * scale) > 0.5 * 10 ** -decimals + 1e-6:
        FAILS.append(f"{label}: STYLE.md says {text}, style.json says {value}")


def quote(doc, sec, text: str) -> str:
    """Text the page repeats must be present in the section it cites."""
    COUNT["quotes"] += 1
    if text not in SEC[doc][sec]:
        FAILS.append(f"{doc} §{sec}: missing {text!r}")
    return text


def claim(doc, sec, text: str, *needles) -> dict:
    src = SEC[doc][sec]
    for n in needles:
        quote(doc, sec, n)
    for num in re.findall(r"\d+(?:\.\d+)?", text):
        COUNT["quotes"] += 1
        if num not in src:
            FAILS.append(f"{doc} §{sec}: claim number {num} not in section: {text}")
    return {"doc": doc, "sec": sec, "text": text}


def beat_of(k: str) -> dict:
    return next(b for b in J["merged"]["evidence"]["beat"] if b["ref"] == BV[k])


def section_of(k: str, role: str) -> dict:
    return next(s for s in J[k]["structure"]["sections"] if s["role"] == role)


def counts_of(k: str) -> dict:
    return J["merged"]["evidence"]["consistency"]["transitions"][BV[k]]


def frames_of(k: str, kind: str):
    return next((t.get("frames") for t in J[k]["transitions"] if t["type"] == kind), None)


LOOK = (  # key, STYLE.md row, page label, chart domain, decimals
    ("L01", "L01 黑位", "L01 黑位", (0, 5), 1),
    ("L50", "L50 中间亮度", "L50 中间亮度", (30, 60), 1),
    ("L99", "L99 白位", "L99 白位", (65, 90), 1),
    ("Lstd", "Lstd 对比", "Lstd 对比", (15, 27), 1),
    ("shadow", "shadow（L<20 占比）", "shadow 暗部占比", (0, 0.4), 2),
    ("chroma", "chroma 饱和度", "chroma 饱和度", (6, 18), 1),
    ("sh_a", "暗部 a", "暗部 a", (-1, 2), 1),
    ("vignette", "暗角", "暗角", (0.7, 1.35), 2),
)
REMOVED = (("sh_b", "暗部 b"), ("mid_a", "中间调 a"), ("mid_b", "中间调 b"), ("hi_a", "高光 a"), ("hi_b", "高光 b"))
SHOTS = (("medium", "中景 medium"), ("wide", "远景 wide"), ("selfie", "自拍/对镜头 selfie"),
         ("detail", "细节 detail"), ("aerial", "航拍 aerial"), ("close", "近景 close"))


def check_numbers() -> None:
    keys = [k for k, _, _ in VIDEOS]
    roles = (("hook 冷开场", "hook"), ("intro 铺垫", "intro"), ("chapter", "chapter"), ("ending", "ending"))
    for name, role in roles:
        r = row(2, name)
        for i, k in enumerate(keys, 1):
            same(f"§2 {name} {k}", r[i], section_of(k, role)["share"], scale=100)
        same(f"§2 {name} merged", r[4], section_of("merged", role)["share"], scale=100)
    for key, name in SHOTS:
        r = row(3, name)
        for i, k in enumerate(keys, 1):
            same(f"§3 {name} {k}", r[i], J[k]["selection"]["shot_types"].get(key, 0), scale=100)
        same(f"§3 {name} merged", r[4], J["merged"]["selection"]["shot_types"][key], scale=100)
    for i, k in enumerate(keys + ["merged"], 1):
        rh = J[k]["rhythm"]
        same(f"§4 median {k}", row(4, "全片镜头中位数")[i], rh["shot_len_s"]["median"])
        same(f"§4 mad {k}", row(4, "MAD")[i], rh["shot_len_s"]["mad"])
        same(f"§4 p10 {k}", row(4, "P10–P90")[i], rh["shot_len_s"]["p10"])
        same(f"§4 p90 {k}", row(4, "P10–P90")[i], rh["shot_len_s"]["p90"], nth=1)
        same(f"§4 cuts/min {k}", row(4, "每分钟剪辑点")[i], rh["cuts_per_min"])
        same(f"§4 n {k}", row(4, "镜头数")[i], rh["shot_len_s"]["n"])
        same(f"§6 hard cut {k}", row(6, "硬切")[i], J[k]["transitions"][0]["share"], scale=100)
    for i, k in enumerate(keys, 1):
        b = beat_of(k)
        same(f"§4 chapter {k}", row(4, "只算 chapter 段")[i], section_of(k, "chapter")["shot_len_s"]["median"])
        same(f"§5 cuts {k}", row(5, "里面的剪辑点")[i], b["n_cuts"])
        same(f"§5 hit {k}", row(5, "落在拍点 ±1 帧")[i], b["hit_rate"], scale=100)
        same(f"§5 baseline {k}", row(5, "随机基线")[i], b["baseline"], scale=100)
        same(f"§5 lift {k}", row(5, "是基线的几倍")[i], b["lift"])
        same(f"§5 downbeat {k}", row(5, "落在重拍上")[i], b["downbeat_hit_rate"], scale=100)
        if row(5, "`beat_alignment`")[i] != b["verdict"]:
            FAILS.append(f"§5 verdict {k}")
        for key, name, *_ in LOOK:
            same(f"§9 {name} {k}", row(9, name)[i], J[k]["grade"]["look"][key]["median"])
        for key, name in REMOVED:
            same(f"§9 {name} {k}", row(9, name)[i], J[k]["grade"]["look"][key]["median"])
        t = J[k]["text"]["title"]
        same(f"§10 y {k}", row(10, "中心 y")[i], t["position"][1])
        same(f"§10 height {k}", row(10, "字高")[i], t["height_frac"], scale=100)
        same(f"§10 stay {k}", row(10, "停留")[i], t["duration_s"])
        same(f"§10 offset {k}", row(10, "所在段落开始后")[i], t["offset_s"])
        same(f"§11 lufs {k}", row(11, "整体响度")[i], J[k]["audio"]["lufs"])
        same(f"§11 bpm {k}", row(11, "BPM")[i], J[k]["audio"]["bpm"]["median"])
        same(f"§11 duck {k}", row(11, "音乐压低（估算）")[i], J[k]["audio"]["duck_db"])
    for key, name, *_ in LOOK:
        v = J["merged"]["grade"]["look"][key]
        same(f"§9 {name} target", row(9, name)[4], v["median"])
        same(f"§9 {name} mad", row(9, name)[4], v["mad"], nth=1)
    t = J["merged"]["text"]["title"]
    same("§10 y merged", row(10, "中心 y")[4], t["position"][1])
    same("§10 height merged", row(10, "字高")[4], t["height_frac"], scale=100)
    same("§10 stay merged", row(10, "停留")[4], t["duration_s"])
    same("§10 offset merged", row(10, "所在段落开始后")[4], t["offset_s"])
    same("§11 lufs merged", row(11, "整体响度")[4], J["merged"]["audio"]["lufs"])
    same("§11 bpm merged", row(11, "BPM")[4], J["merged"]["audio"]["bpm"]["median"])

    s = J["snow"]
    rh = s["rhythm"]["shot_len_s"]
    quote("snow", 4, f"中位数 {fx(rh['median'], 2)} s ± {fx(rh['mad'], 2)}（MAD），P10 {fx(rh['p10'], 2)} s，P90 {fx(rh['p90'], 2)} s，n = {rh['n']}")
    b = beat_of("snow")
    quote("snow", 5, f"其中 {pct(b['hit_rate'])} 落在拍点")
    quote("snow", 5, f"基线是 {pct(b['baseline'])}")
    quote("snow", 5, f"落在重拍上的 {pct(b['downbeat_hit_rate'])}")
    quote("snow", 6, f"{pct(s['transitions'][0]['share'])} | — | 所有地方")
    quote("snow", 6, f"中位数 {frames_of('snow', 'dissolve')} 帧")
    quote("snow", 6, f"中位数 {frames_of('snow', 'dip_to_black')} 帧")
    lk = s["grade"]["look"]
    for key, d in (("L01", 1), ("L50", 1), ("L99", 1), ("Lstd", 1), ("shadow", 2), ("chroma", 1)):
        quote("snow", 9, f"{fx(lk[key]['median'], d)} ± {fx(lk[key]['mad'], d)}")
    for a, b_ in (("sh_a", "sh_b"), ("mid_a", "mid_b"), ("hi_a", "hi_b")):
        quote("snow", 9, f"{signed(lk[a]['median']).lstrip('+') if lk[a]['median'] < 0 else signed(lk[a]['median'])} / {signed(lk[b_]['median'])}")
    quote("snow", 11, f"{fx(s['audio']['lufs'])} LUFS")


# ---------------------------------------------------------------- full text

def render_table(block: list[str]) -> str:
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in block]
    rows = [r for r in rows if not all(re.fullmatch(r":?-+:?", c) for c in r)]
    head = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
    body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_list(block: list[str]) -> str:
    items = [(len(l) - len(l.lstrip()), l.strip()) for l in block]

    def walk(i, ind):
        name = "ol" if re.match(r"\d+\. ", items[i][1]) else "ul"
        parts = []
        while i < len(items) and items[i][0] >= ind:
            if items[i][0] > ind:
                sub, i = walk(i, items[i][0])
                parts[-1] = parts[-1][:-5] + sub + "</li>"
                continue
            parts.append(f"<li>{inline(re.sub(r'^(?:-|[0-9]+[.]) ', '', items[i][1]))}</li>")
            i += 1
        return f"<{name}>{''.join(parts)}</{name}>", i

    return walk(0, items[0][0])[0]


LIST_LINE = re.compile(r"\s*(?:-|\d+\.) ")


def md_to_html(md: str, prefix: str) -> str:
    lines, out, i = md.splitlines(), [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("# "):
            out.append(f'<h3 class="doc-title">{inline(line[2:])}</h3>')
            i += 1
        elif line.startswith("## "):
            m = re.match(r"(\d+)\. ", line[3:])
            anchor = f"{prefix}-s{m[1]}" if m else f"{prefix}-log"
            out.append(f'<h4 id="{anchor}">{inline(line[3:])}</h4>')
            i += 1
        elif line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(render_table(block))
        elif LIST_LINE.match(line):
            block = []
            while i < len(lines) and LIST_LINE.match(lines[i]):
                block.append(lines[i])
                i += 1
            out.append(render_list(block))
        else:
            out.append(f"<p>{inline(line)}</p>")
            i += 1
    return "\n".join(out)


def plain(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\s+", "", s)


def check_full_text(md: str, rendered: str, doc: str) -> None:
    text = plain(rendered)
    for line in md.splitlines():
        if not line.strip() or re.fullmatch(r"\|[\s:|-]+\|", line.strip()):
            continue
        pieces = line.strip().strip("|").split("|") if line.startswith("|") else [line]
        for p in pieces:
            p = re.sub(r"^\s*(?:#+|-|\d+\.)\s+", "", p) if not line.startswith("##") else re.sub(r"^#+\s+", "", p)
            needle = re.sub(r"\s+", "", p.replace("**", "").replace("`", ""))
            COUNT["lines"] += 1
            if needle and needle not in text:
                FAILS.append(f"{doc} full text lost: {p[:60]}")
    heads = re.findall(r'<h4 id="[a-z]+-(s\d+|log)"', rendered)
    if len(heads) != 13:
        FAILS.append(f"{doc}: expected 12 sections and 炼化记录, found {len(heads)}")


def toc(md: str, prefix: str) -> str:
    links = []
    for m in re.finditer(r"^## (?:(\d+)\. )?(.+)$", md, re.M):
        anchor = f"{prefix}-s{m[1]}" if m[1] else f"{prefix}-log"
        links.append(f'<a href="#{anchor}">{"§" + m[1] + " " if m[1] else ""}{esc(m[2])}</a>')
    return f'<nav class="toc" aria-label="小节索引">{"".join(links)}</nav>'


# ---------------------------------------------------------------- charts

W = 760


def text(x, y, s, cls="t", anchor="start") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}">{esc(s)}</text>'


def hair(x1, y1, x2, y2, cls="grid") -> str:
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" class="{cls}"/>'


def dot(x, y, r, cls) -> str:
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" class="dot f-{cls}"/>'


def box(x, y, w, h, cls, rx=0) -> str:
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{h:.1f}" rx="{rx}" class="{cls}"/>'


def mark(x, y, w, h, tip: str, inner: str) -> str:
    return (f'<g class="mark" tabindex="0" role="img" aria-label="{attr(tip)}" data-tip="{attr(tip)}">'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" class="hit"/>{inner}</g>')


def scale(d0, d1, x0, x1):
    return lambda v: x0 + (v - d0) / (d1 - d0) * (x1 - x0)


def svg(h, label, body: list[str]) -> str:
    return f'<div class="chart"><svg viewBox="0 0 {W} {h}" role="group" aria-label="{attr(label)}">{"".join(body)}</svg></div>'


def legend(*extra) -> str:
    items = "".join(f'<span><i class="sw s-{k}"></i>{n}</span>' for k, n, _ in VIDEOS)
    return f'<p class="legend">{items}{"".join(extra)}</p>'


def swatch(k, name) -> str:
    return f'<i class="sw s-{k}"></i>{esc(name)}'


def data_table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{inline(h)}</th>" for h in head)
    tb = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="data"><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>'


def chart_rhythm() -> str:
    x0, x1 = 92, 540
    X = scale(0, 12, x0, x1)
    top, pitch = 40, 44
    rows = [(k, n) for k, n, _ in VIDEOS] + [("merged", "合并后")]
    bottom = top + pitch * len(rows) - 12
    b = [text(x0, 14, "镜头时长（秒）：细条 P10–P90，圆点中位数", "tm"),
         text(612, 14, "每分钟剪辑点", "tm", "middle"), text(706, 14, "各章之间差", "tm", "middle")]
    for v in range(0, 13, 2):
        b += [hair(X(v), 24, X(v), bottom), text(X(v), bottom + 16, str(v), "tm", "middle")]
    b.append(hair(0, top + pitch * 3 - 16, W, top + pitch * 3 - 16))
    for i, (k, n) in enumerate(rows):
        y = top + pitch * i + 6
        s = J[k]["rhythm"]["shot_len_s"]
        cuts = fx(J[k]["rhythm"]["cuts_per_min"])
        spread = pct(J[k]["evidence"]["consistency"]["shot_len_s"]["spread"]) if k != "merged" else "—"
        tip = f"{n}\n中位数 {fx(s['median'], 2)} s，MAD {fx(s['mad'], 2)}\nP10 {fx(s['p10'], 2)} s，P90 {fx(s['p90'], 2)} s\n{s['n']} 个镜头"
        inner = (box(X(s["p10"]), y - 5, X(s["p90"]) - X(s["p10"]), 10, f"r-{k}", 5) + dot(X(s["median"]), y, 6, k)
                 + text(X(s["median"]), y - 11, fx(s["median"], 2), "tv", "middle"))
        b.append(mark(x0, y - 21, x1 - x0, 40, tip, inner))
        b += [text(0, y + 4, n), text(612, y + 4, cuts, "tv", "middle"), text(706, y + 4, spread, "tv", "middle")]
    return svg(bottom + 22, "每条视频的镜头时长区间和中位数", b)


def transition_label(k: str, kind: str) -> str:
    c = counts_of(k).get(kind, 0)
    f = frames_of(k, kind)
    if not c:
        return "0"
    if kind == "other":
        quote("snow", 6, "约 6 帧")
        return f"{c} 次 · 约 6 帧 · 拍摄时做的，已从 transitions 去掉"
    if kind == "dip_to_black" and k == "snow":
        quote("merged", 6, f"{c} 条标签（8 处），{f} 帧")
        return f"{c} 条标签（8 处）· {f} 帧"
    if kind == "dissolve" and k == "island":
        quote("merged", 6, f"{c} 次，约 {f} 帧，都 `sure: false`")
        return f"{c} 次 · 约 {f} 帧 · 都 sure: false"
    if kind == "dissolve" and k == "kula":
        quote("merged", 6, f"{c} 次，约 {f} 帧")
        return f"{c} 次 · 约 {f} 帧"
    quote("merged", 6, f"{c} 次，{f} 帧")
    return f"{c} 次 · {f} 帧"


def chart_transitions() -> str:
    x0, x1 = 92, 372
    X = scale(0, 12, x0, x1)
    groups = (("黑场", "dip_to_black"), ("叠化", "dissolve"), ("手挡镜头", "other"))
    top, lane, gap = 26, 18, 16
    bottom = top + len(groups) * (lane * 3 + gap) - gap + 4
    b = [text(x0, 14, "次数（硬切不画，见上面的占比）", "tm")]
    for v in (0, 4, 8, 12):
        b += [hair(X(v), 22, X(v), bottom), text(X(v), bottom + 16, str(v), "tm", "middle")]
    for g, (name, kind) in enumerate(groups):
        y0 = top + g * (lane * 3 + gap)
        tip = [name] + [f"{n} {transition_label(k, kind)}" for k, n, _ in VIDEOS]
        inner = []
        for i, (k, _, _) in enumerate(VIDEOS):
            y = y0 + lane * i
            c = counts_of(k).get(kind, 0)
            if c:
                inner.append(box(x0, y + 2, X(c) - x0, 10, f"f-{k}", 2))
            inner.append(text(X(c) + 6, y + 11, transition_label(k, kind), "tv2"))
        b.append(mark(0, y0, W, lane * 3, "\n".join(tip), "".join(inner)))
        b.append(text(0, y0 + lane + 11, name))
    return svg(bottom + 22, "每条视频的黑场、叠化和手挡镜头次数", b)


def chart_shots() -> str:
    x0, x1 = 128, 560
    X = scale(0, 50, x0, x1)
    top, pitch = 28, 42
    bottom = top + pitch * len(SHOTS) - 8
    b = [text(x0, 14, "占比：三根细条是三条视频，竖线是合并后", "tm"),
         text(612, 14, "合并后", "tm", "middle"), text(690, 14, "STYLE.md 标注", "tm", "middle")]
    for v in range(0, 51, 10):
        b += [hair(X(v), 22, X(v), bottom), text(X(v), bottom + 16, f"{v}%", "tm", "middle")]
    for i, (key, name) in enumerate(SHOTS):
        y = top + pitch * i
        vals = {k: J[k]["selection"]["shot_types"].get(key, 0) for k, _, _ in VIDEOS}
        merged = J["merged"]["selection"]["shot_types"][key]
        inner = [box(x0, y + j * 10, X(vals[k] * 100) - x0, 8, f"f-{k}", 2) for j, (k, _, _) in enumerate(VIDEOS) if vals[k]]
        inner.append(box(X(merged * 100) - 1, y - 2, 2, 32, "f-merged"))
        tip = "\n".join([name] + [f"{n} {pct(vals[k])}" for k, n, _ in VIDEOS] + [f"合并后 {pct(merged)}"])
        b.append(mark(x0, y - 4, x1 - x0, 36, tip, "".join(inner)))
        verdict = "不一致" if "不一致" in row(3, name)[5] else "一致"
        b += [text(0, y + 19, name.split(" ")[0]), text(612, y + 19, pct(merged), "tv", "middle"), text(690, y + 19, verdict, "t", "middle")]
    return svg(bottom + 22, "三条视频的景别占比", b)


def chart_grade_kept() -> str:
    x0, x1 = 150, 530
    top, pitch = 34, 50
    b = [text(x0, 14, "灰带是目标（合并后 ± MAD），竖线是中位数，三个点从上到下是孤岛、库拉岗日、雪山。每行刻度各自独立", "tm")]
    for i, (key, _, name, (d0, d1), dec) in enumerate(LOOK):
        X = scale(d0, d1, x0, x1)
        y = top + pitch * i + 14
        t = J["merged"]["grade"]["look"][key]
        lo, hi = t["median"] - t["mad"], t["median"] + t["mad"]
        vals = {k: J[k]["grade"]["look"][key]["median"] for k, _, _ in VIDEOS}
        out = [n for k, n, _ in VIDEOS if not lo <= vals[k] <= hi]
        inner = [box(X(lo), y - 16, X(hi) - X(lo), 32, "band"), box(X(t["median"]) - 1, y - 16, 2, 32, "f-merged")]
        for j, (k, _, _) in enumerate(VIDEOS):
            inner.append(dot(X(vals[k]), y + (j - 1) * 10, 4, k))
        fmt = (lambda v: signed(v, dec)) if key == "sh_a" else (lambda v: fx(v, dec))
        target = f"{fmt(t['median'])} ± {fx(t['mad'], dec)}"
        tip = "\n".join([name, f"目标 {target}"] + [f"{n} {fmt(vals[k])}" for k, n, _ in VIDEOS])
        b.append(mark(x0, y - 20, x1 - x0, 40, tip, "".join(inner)))
        b += [text(0, y + 4, name), text(x0 - 8, y + 4, fmt(d0) if key == "sh_a" else str(d0), "tm", "end"), text(x1 + 8, y + 4, fmt(d1) if key == "sh_a" else str(d1), "tm"),
              text(572, y + 4, target, "tv")]
        if out:
            b.append(text(662, y + 4, "、".join(out) + "超出", "t"))
    return svg(top + pitch * len(LOOK), "保留的 8 项调色目标和三条视频的值", b)


def chart_grade_removed() -> str:
    x0, x1 = 128, 560
    X = scale(-12, 4, x0, x1)
    top, pitch = 28, 42
    rows = REMOVED + (("sh_a", "暗部 a"),)
    bottom = top + pitch * len(rows) - 6
    b = [text(x0, 14, "Lab 色偏，同一条刻度。a 负偏绿、正偏品红；b 负偏蓝、正偏黄", "tm"),
         text(612, 14, "最大差", "tm", "middle"), text(690, 14, "处理", "tm", "middle")]
    for v in (-12, -8, -4, 0, 4):
        b += [hair(X(v), 22, X(v), bottom, "axis" if v == 0 else "grid"), text(X(v), bottom + 16, signed(v, 0) if v else "0", "tm", "middle")]
    b.append(hair(0, top + pitch * 5 - 8, W, top + pitch * 5 - 8))
    for i, (key, name) in enumerate(rows):
        y = top + pitch * i + 12
        vals = {k: J[k]["grade"]["look"][key]["median"] for k, _, _ in VIDEOS}
        spread = max(vals.values()) - min(vals.values())
        inner = [hair(X(min(vals.values())), y, X(max(vals.values())), y, "range")]
        for j, (k, _, _) in enumerate(VIDEOS):
            inner.append(dot(X(vals[k]), y + (j - 1) * 9, 4, k))
        tip = "\n".join([name, f"最大差 {fx(spread)}"] + [f"{n} {signed(vals[k])}" for k, n, _ in VIDEOS])
        b.append(mark(x0, y - 17, x1 - x0, 34, tip, "".join(inner)))
        b += [text(0, y + 4, name), text(612, y + 4, fx(spread), "tv", "middle"), text(690, y + 4, "保留" if key == "sh_a" else "去掉", "t", "middle")]
    return svg(bottom + 22, "去掉的色偏项在三条视频的值", b)


def chart_beat() -> str:
    x0, x1 = 92, 540
    X = scale(0, 40, x0, x1)
    top, pitch = 42, 46
    bottom = top + pitch * 3 - 14
    b = [text(x0, 14, "非人声段的剪辑点：灰点是随机基线，彩点是落在拍点 ±1 帧的比例", "tm"),
         text(600, 14, "是基线的", "tm", "middle"), text(690, 14, "beat_alignment", "tm", "middle")]
    for v in range(0, 41, 10):
        b += [hair(X(v), 24, X(v), bottom, "axis" if v == 30 else "grid"), text(X(v), bottom + 16, f"{v}%", "tm", "middle")]
    b.append(text(X(30) + 4, 32, "30% 线", "tm"))
    for i, (k, n, _) in enumerate(VIDEOS):
        y = top + pitch * i + 8
        a = beat_of(k)
        x_base, x_hit = X(a["baseline"] * 100), X(a["hit_rate"] * 100)
        inner = (hair(x_base, y, x_hit, y, f"l-{k}") + dot(x_base, y, 4, "base") + dot(x_hit, y, 6, k)
                 + text(x_hit, y - 11, pct(a["hit_rate"]), "tv", "middle"))
        tip = f"{n}\n命中 {pct(a['hit_rate'])}，基线 {pct(a['baseline'])}\n重拍 {pct(a['downbeat_hit_rate'])}，共 {a['n_cuts']} 个剪辑点"
        b.append(mark(x0, y - 21, x1 - x0, 40, tip, inner))
        b += [text(0, y + 4, n), text(600, y + 4, f"{fx(a['lift'])} 倍", "tv", "middle"), text(690, y + 4, a["verdict"], "tcode", "middle")]
    return svg(bottom + 22, "每条视频的卡点命中率和随机基线", b)


# ---------------------------------------------------------------- page sections

def claims_list(items: list[dict]) -> str:
    rows = []
    for c in items:
        pre = PREFIX[c["doc"]]
        label = ("雪山 " if c["doc"] == "snow" else "") + f"§{c['sec']}"
        rows.append(f'<li><span>{inline(c["text"])}</span><a class="sec" href="#{pre}-s{c["sec"]}">{label}</a></li>')
    return f'<ul class="claims">{"".join(rows)}</ul>'


def build_conclusions() -> str:
    m = "merged"
    ok = [
        claim(m, 1, "画幅 16:9 横屏，无遮幅；帧率 29.97，三条都是。", "16:9 横屏，无遮幅", "三条都是"),
        claim(m, 2, "骨架：短冷开场，短铺垫，然后一章一件事、按时间走，章节占九成左右（89.2%、86.1%、93.0%）。", "骨架一致"),
        claim(m, 3, "景别排序：中景最多（38.9–47.9%），远景第二（26.9–35.2%），自拍 9.7–10.4%，细节 8.5–9.7%，近景几乎不用。", "三条都最多", "三条都第二", "几乎不用"),
        claim(m, 3, "偏好会动的机位：稳定前进或后退的镜头占 24.9%、20.8%、25.0%，抽看的 72 个都是拍摄时的运动。", "偏好会动的机位：一致"),
        claim(m, 3, "暗的镜头照用：三条都留着 L50 很低的夜景（约 19、14、11）。", "暗的镜头照用：一致", "约 19", "约 14", "约 11"),
        claim(m, 4, "全片节奏：镜头中位数 4.04、3.54、3.97 s，只差 12.6%。", "全片节奏一致", "12.6%"),
        claim(m, 5, "卡点高于随机：都是基线的 3 倍以上，剪辑点中位数都晚 1 帧。", "高于随机：一致", "3 倍以上", "晚 1 帧"),
        claim(m, 6, "硬切为主：98.6%、98.4%、96.5%，包括绝大多数章节之间。", "硬切为主：一致", "绝大多数章节之间"),
        claim(m, 6, "黑场用在时间跳跃上，帧数中位数 8–12 帧。", "黑场用在时间跳跃上：一致", "8–12 帧"),
        claim(m, 6, "没有甩镜、急推、闪白；候选看过事件条，都是误报。", "没有甩镜、急推、闪白：一致", "都是误报"),
        claim(m, 7, "数字推拉 0：测到稳定推 254 个、拉 92 个，判了 72 个，没有一个是剪辑里加的。", "数字推拉：0，一致"),
        claim(m, 9, "调色只留 8 项：黑位、中间亮度、白位、对比、暗部占比、饱和度、暗部 a、暗角。核对时用子包。", "只留三条一致的项", "调色核对用子包"),
        claim(m, 10, "字幕：全片底部居中烧录，白色细黑体；中心 y 0.946、0.956、0.955，字高 4.4%、4.6%、4.4%。", "字幕：一致"),
        claim(m, 11, "人声主导，音乐铺底。只按测量推断，Claude 听不到。", "人声主导，音乐铺底：一致", "Claude 听不到"),
    ]
    if set(J[m]["grade"]["look"]) != {k for k, *_ in LOOK}:
        FAILS.append("grade.look no longer holds the 8 listed items")
    diff = [
        claim(m, 1, "时长 29:22–55:12，实际按 brief。", "29:22–55:12", "**不一致**"),
        claim(m, 2, "章节开头：多数硬切；库拉岗日 4 章用黑底短卡，雪山 4 章从黑场进入，孤岛只有 1 章从黑场进入。", "章节开头不一致"),
        claim(m, 2, "片尾：孤岛 3 个长航拍；库拉岗日照片连放、手写标识和彩蛋；雪山照片连放接太空画面。", "片尾不一致"),
        claim(m, 3, "航拍占比 8.2%、2.6%、5.7%。", "**不一致**"),
        claim(m, 3, "固定机位 27.2%、40.8%、32.5%。", "27.2%、40.8%、32.5%：**不一致**"),
        claim(m, 4, "各章之间的节奏：三条内部各章差 39.7%、61.5%、107%。", "各章之间不一致"),
        claim(m, 5, "够不够「卡点」：过 30% 线的是两条李现的片子，孤岛没过。可能是题材或对话的差别，三条分不开。", "够不够「卡点」：不一致", "三条分不开"),
        claim(m, 6, "叠化：14 次里 11 次来自雪山，用在地图后面和同机位表现时间过去。", "叠化：不一致", "14 次里 11 次来自雪山"),
        claim(m, 8, "信息手法三条都会加，加什么不一样：地图和照片插入三条都有，多少不一致；时间戳以雪山为主（5 处），海拔和氧气角标以库拉岗日为主（5 处）。", "加什么不一样", "雪山为主", "库拉岗日为主"),
        claim(m, 9, "色偏 5 项三条差得多（暗部 b、中间调 a 和 b、高光 a 和 b），不写目标。", "三条差得多"),
        claim(m, 10, "地点标题：字体每条都不一样；共同点是近白、没有描边和底板、淡入、字高约 6–7%。", "地点标题（style.text.title）：不一致", "字高约 6–7%"),
        claim(m, 10, "黑底短卡：孤岛 2 张，库拉岗日 5 张，雪山没有。按 brief 有需要才加。", "黑底短卡（style.text.chapter_card）：不一致"),
    ]
    two = [
        claim(m, 5, "过 30% 的卡点线：命中率 30.8%、35.1%，孤岛 19.7%。", "过 30% 的线的是两条李现的片子"),
        claim(m, 8, "手写系列标题：库拉岗日放在冷开场末尾，雪山放在第 49 分钟山顶日落。", "| 手写系列标题 | 无 | 冷开场末尾 | 第 49 分钟山顶日落 | 两条李现的片子 |"),
        claim(m, 3, "开场片段明确取自后面的段落。", "两条李现的片子明确取自后面的段落"),
        claim(m, 9, "中间调偏蓝：两条李现的片子满眼雪和蓝调，孤岛接近中性。这是题材带来的，不能当成调色习惯。", "中间调 b 不能当成博主的调色习惯", "这是题材带来的"),
    ]
    one = [
        claim(m, 6, "手挡镜头转场：雪山 1 次，拍摄时做的。", "手挡镜头转场", "**单一来源**"),
        claim(m, 8, "人名条：孤岛 4 处。", "| 人名条 | 4 处 | 无 | 无 | **单一来源** |"),
        claim(m, 2, "赞助段：只有孤岛有。plan_cut 只在 brief 给了 `interlude_tags` 时才剪。", "赞助段只有孤岛有", "`interlude_tags`"),
    ]
    doubt = [
        claim(m, 1, "分辨率 3840×2160 是 style.json 的默认交付规格，测量用的都是 1080p 下载。按用户素材定。", "1080p 下载：**存疑**"),
        claim(m, 5, "速度：beat_this 测 115、111、121.5 BPM，剪辑点附近的拍距折算却只有 91、91、88。命中率是按 beat_this 的拍点算的。", "**存疑**", "115、111、121.5", "91、91、88"),
        claim(m, 7, "慢动作：三条都从总览图看不出，没标，没有逐个查。", "**存疑**：没有逐个查"),
        claim(m, 7, "变速曲线记 0：213 个变速候选没有逐个看，多半是机位起停。", "多半是机位起停。**存疑**"),
        claim(m, 8, "颗粒在 B 站二次压缩之后看不准。", "看不准：**存疑**"),
        claim(m, 10, "字幕的描边、阴影或底条看不清，style.json 默认填了描边。", "看不清：**存疑**（style.json 默认填了描边）"),
        claim(m, 11, "BPM 合并值 115.4，原因同上面的速度。", "115.4（**存疑**"),
        claim(m, 11, "音乐压低 0.5 dB 是估算。", "0.5 dB（**存疑**）"),
    ]
    snow_doubt = [
        claim("snow", 3, "城区航拍有没有计入 5.7% 的航拍，没有逐个确认。", "没有逐个确认：**存疑**"),
        claim("snow", 9, "极光一章中间调 b 的 +13.2 不能当调色目标，这一项没有单独核实；片尾的 −24.9 含太空画面。", "没有单独核实", "含太空画面"),
        claim("snow", 10, "北角台地地名的拼写没有逐字核对。", "拼写没有逐字核对：**存疑**"),
    ]
    quote(m, 5, "J/L cut 没有测（Claude 听不到）")
    quote(m, 11, "没有标音效")
    groups = lambda t, kind, n, note, body: (
        f'<div class="group"><h3>{tag(t, kind)}<span class="n">{n} 项</span></h3><p class="group-note">{note}</p>{body}</div>')
    doubt_total = len(doubt) + len(snow_doubt)
    tally = (f'<p class="tally"><b>{len(ok)}</b> 项一致 · <b>{len(diff)}</b> 项不一致 · <b>{len(two)}</b> 项只有两条李现的片子符合 · '
             f'<b>{len(one)}</b> 项单一来源 · <b>{doubt_total}</b> 项存疑</p>')
    left = groups("一致", "ok", len(ok), "三条都这样，可以当博主风格。范围只限这三条。", claims_list(ok))
    right = (groups("不一致", "diff", len(diff), "三条不同，写范围，自由发挥。", claims_list(diff))
             + groups("两条李现的片子", "two", len(two), "孤岛不符合，可能只是题材，不写成一致。", claims_list(two)))
    low_l = groups("单一来源", "one", len(one), "只有一条这样。", claims_list(one))
    low_r = (groups("存疑", "doubt", doubt_total, "测量或判读不可靠。前 8 项来自博主级包，后 3 项是雪山子包另有的。", claims_list(doubt + snow_doubt))
             + '<p class="group-note">另外没有测的：J/L cut（§5）；音效没有标（§11）。</p>')
    return (f'<section id="verdict"><h2>结论</h2>{tally}<div class="cols">{left}<div class="stack">{right}</div></div>'
            f'<div class="cols">{low_l}<div class="stack">{low_r}</div></div>'
            '<p class="note">标注沿用 STYLE.md 的原话。雪山子包的「一致」指 14 章之间一致，和这里的三条一致不是一回事。</p></section>')


def judge(title: str, kind: tuple[str, str], rows: list[tuple[str, str]], extra: str = "") -> str:
    body = "".join(f'<div class="kv{" ask" if k == "要你定" else ""}"><dt>{k}</dt><dd>{v}</dd></div>' for k, v in rows)
    return f'<article class="judge"><h3>{inline(title)} {tag(*kind)}</h3><dl>{body}</dl>{extra}</article>'


def build_judgments() -> str:
    m = "merged"
    look = {k: J[k]["grade"]["look"] for k, _, _ in VIDEOS}
    quote(m, 9, "色偏类绝对差超过 2 就去掉")
    quote(m, 9, "`compare.py look` 会跳过没有的项")
    quote(m, 9, "`hue_hist` 也去掉了，三条的主色比例差得多")
    quote(m, "log", "`grade.look` 去掉 `sh_b`、`mid_a`、`mid_b`、`hi_a`、`hi_b`、`hue_hist`")
    for key, _ in REMOVED:
        if key in J[m]["grade"]["look"]:
            FAILS.append(f"{key} is still in grade.look")
    rows_a = []
    for key, name in REMOVED + (("sh_a", "暗部 a（留下的，作对比）"),):
        vals = [look[k][key]["median"] for k, _, _ in VIDEOS]
        rows_a.append([esc(name)] + [signed(v) for v in vals] + [fx(max(vals) - min(vals))])
    hi_b = [look[k]["hi_b"]["median"] for k, _, _ in VIDEOS]
    if not all(v < 0 for v in hi_b):
        FAILS.append("hi_b is no longer negative in all three")
    a = judge("去掉的调色项", ("手改", "edit"), [
        ("现状", "<code>grade.look</code> 去掉 <code>sh_b</code>、<code>mid_a</code>、<code>mid_b</code>、<code>hi_a</code>、<code>hi_b</code>、<code>hue_hist</code>，只留 8 项。<code>compare.py look</code> 会跳过没有的项。"),
        ("依据", "色偏类绝对差超过 2 就去掉。中间调 b 的差主要来自题材。<code>hue_hist</code> 去掉是因为三条的主色比例差得多。"),
        ("要你定", f"博主级包不再核对色偏，调色核对全靠子包，接受吗？另外高光 b 三条都是负数（{'、'.join(signed(v) for v in hi_b)}），方向一致，差值超过 2 所以去掉了。文字还写「高光都略偏蓝」，目标里没有这一项，这样处理合适吗？"),
    ], data_table(["项", "孤岛", "库拉岗日", "雪山", "最大差"], rows_a))

    quote(m, 10, "装饰体只是 draft 按标题个数算出来的")
    quote(m, 12, "地点标题的字体按素材选")
    if "装饰体" in SEC[m]["log"]:
        FAILS.append("炼化记录 now mentions 装饰体; the card text needs a rewrite")
    fonts = row(10, "字体类别")
    b = judge("标题字体用装饰体", ("draft 结果，不是手改", "draft"), [
        ("现状", f"<code>text.title.font_class</code> 是 {esc(J[m]['text']['title']['font_class'])}，备选 {esc(J[m]['text']['title']['fallback_fonts'][0])}。炼化记录里没有这一项，写在第 10 节。"),
        ("依据", f"draft 按标题个数取最多的那类，雪山 {row(10, '个数')[3]} 个标题占了多数。三条分别是：{esc(fonts[1])}、{esc(fonts[2])}、{esc(fonts[3])}。第 12 节把「地点标题的字体按素材选」列在手动收尾里。"),
        ("要你定", "三条的字体各不相同，所以这个默认值说不上是博主的习惯。留着靠手动收尾换，还是换成中性的默认值？我建议换成黑体，和字幕、黑底短卡一致，忘了换也不突兀。"),
    ])

    quote(m, "log", "`transitions` 去掉 `other`")
    quote(m, 6, "免得 plan_cut 按占比抽中")
    quote(m, 6, "计数还在 `evidence.consistency.transitions` 里")
    quote("snow", 6, "手挡镜头转场，铺垫里约 86 s，拍摄时做的")
    quote("snow", "log", "`transitions` 去掉 `other`")
    other = counts_of("snow").get("other", 0)
    total = sum(t["share"] for t in J[m]["transitions"])
    if any(t["type"] == "other" for t in J[m]["transitions"] + J["snow"]["transitions"]):
        FAILS.append("transitions still has other")
    c = judge("去掉手挡镜头转场", ("手改", "edit"), [
        ("现状", f"博主级包和雪山子包的 <code>transitions</code> 都去掉了 <code>other</code>，计数留在 <code>evidence.consistency.transitions</code>（雪山 {other} 次）。"),
        ("依据", f"只有雪山铺垫里约 86 s 这 {other} 次，拍摄时做的，属于单一来源。留着的话 plan_cut 可能按占比抽中。余下占比合计 {fx(total, 4)}，<code>plan_cut.py</code> 按总和抽取，不受影响。"),
        ("要你定", "接受它只留记录、不进抽选吗？"),
    ])

    t = {k: J[k]["text"]["title"] for k, _, _ in VIDEOS}
    quote(m, "log", "`text.title.position` 的 y 从 0.501 改成 0.391，`offset_s` 从 34.78 改成 7.47，都取每条视频的中位数")
    quote("island", 10, "铺垫开始后 7.5 s 出现")
    quote("kula", 10, "开始后 8.9 s")
    quote("kula", 10, "167.2")
    quote("snow", 10, "34.78")
    quote("snow", 10, "地名标题在所在镜头里的中位时间点")
    ys = [t[k]["position"][1] for k, _, _ in VIDEOS]
    offs = [t[k]["offset_s"] for k, _, _ in VIDEOS]
    draft = [t["island"]["offset_s"], 167.2, 34.78]
    d = judge("标题位置和时机取中位数", ("手改", "edit"), [
        ("现状", f"y 从 0.501 改成 {J[m]['text']['title']['position'][1]}，<code>offset_s</code> 从 34.78 改成 {J[m]['text']['title']['offset_s']}。炼化记录写的是「都取每条视频的中位数」。"),
        ("依据", f"y 没有问题，三条的中位数就是 {fx(statistics.median(ys), 3)}。<code>offset_s</code> 的三个数基准不同，孤岛相对段落，库拉岗日和雪山相对所在镜头。统一到段落基准，中位数是 {fx(statistics.median(draft), 2)}，正好是改掉的那个值。"),
        ("要你定", f"{fx(statistics.median(offs), 2)} 这个值本身能用，段落开始后几秒出标题说得通，但它不是同一基准的中位数。我建议保留这个值，第 10 节和炼化记录改写成取舍。见发现 1。"),
    ], data_table(["项", "孤岛", "库拉岗日", "雪山", "中位数", "style.json"], [
        ["中心 y"] + [fx(v, 3) for v in ys] + [fx(statistics.median(ys), 3), fx(J[m]["text"]["title"]["position"][1], 3)],
        ["<code>offset_s</code> 现值（s）"] + [str(v) for v in offs] + [fx(statistics.median(offs), 2), str(J[m]["text"]["title"]["offset_s"])],
        ["这个值的基准", "段落", "所在镜头", "所在镜头", "混合", "—"],
        ["段落基准的 draft 值（s）"] + [str(v) for v in draft] + [fx(statistics.median(draft), 2), "—"],
    ]))
    other_edits = quote(m, "log", "`summary`（draft 留空）、`grade.summary`、`audio.mood`、`selection.opening`（draft 只取单条）重写")
    other_note = f'<p class="note">炼化记录里另外两项不改数字：{inline(other_edits)}；<code>effects</code> 每项补 <code>ref</code>。见 <a href="#links-log">炼化记录</a>。</p>'

    # findings from cross-checking the sources
    f1 = judge("发现 1：`offset_s` 的基准混了", ("复核发现", "found"), [
        ("问题", "<code>references/style-schema.md:119</code> 写的是「章节开始后多久出现」，<code>scripts/plan_cut.py:420</code> 也是段落开始时间加这个值。三个子包里只有孤岛是这个基准。<code>links/STYLE.md</code> 第 10 节把三个数都放在「所在段落开始后」这一行。"),
        ("影响", f"plan_cut 用这两个子包时，标题会出现在段落开始后 {t['kula']['offset_s']} s 和 {t['snow']['offset_s']} s，不是原来那个镜头里。时间本身能用，问题是文档的说法和数字的含义对不上。"),
        ("要你定", "改第 10 节的行名，写明每个数的基准。子包的值要不要改回段落基准，由你定；改回去，库拉岗日是 167.2 s，雪山是 34.78 s，标题会很晚才出现，我不建议。"),
    ])
    cols = [t[k]["color"] for k, _, _ in VIDEOS]
    med_col = [statistics.median(c[i] for c in cols) for i in range(3)]
    heights = [t[k]["height_frac"] for k, _, _ in VIDEOS]
    stays = [t[k]["duration_s"] for k, _, _ in VIDEOS]
    outs = [t[k]["out"] for k, _, _ in VIDEOS]
    mt = J[m]["text"]["title"]

    def chip(c):
        h = hexcolor(c)
        return f'<i class="chip" style="background:{h}"></i><code>{h}</code>'

    f2 = judge("发现 2：标题的字高和颜色仍按标题个数算", ("复核发现", "found"), [
        ("问题", f"y 和时机改成了按视频取，字高和颜色没有跟着改。字高 {pct(mt['height_frac'], 2)}，按视频的中位数是 {pct(statistics.median(heights), 2)}；颜色就是雪山的值。停留时长两种算法都是 {mt['duration_s']} s；出场方式三条是 {'、'.join(outs)}，取多数也是 {mt['out']}，这两项没有问题。"),
        ("影响", "差别很小，颜色都是近白。问题在炼化记录的取法不统一。"),
        ("要你定", "把字高和颜色也改成按视频取，还是在炼化记录里写明这两项没改？"),
    ], data_table(["项", "孤岛", "库拉岗日", "雪山", "按视频的中位数", "style.json"], [
        ["字高"] + [pct(v, 2) for v in heights] + [pct(statistics.median(heights), 2), pct(mt["height_frac"], 2)],
        ["颜色"] + [chip(c) for c in cols] + [chip(med_col), chip(mt["color"])],
        ["停留（s）"] + [str(v) for v in stays] + [fx(statistics.median(stays), 2), str(mt["duration_s"])],
    ]))
    card = {k: J[k]["text"]["chapter_card"] for k in ("island", "kula")}
    mc = J[m]["text"]["chapter_card"]
    quote("island", 10, "在讲传统那一章里")
    f3 = judge("发现 3：黑底短卡也是按张数合并的", ("复核发现", "found"), [
        ("问题", f"字高 {pct(mc['height_frac'], 2)} 主要来自库拉岗日的 5 张（{pct(card['kula']['height_frac'], 2)}），孤岛的 2 张是 {pct(card['island']['height_frac'], 2)}。<code>offset_s</code> {mc['offset_s']} s 只反映库拉岗日的开章卡，孤岛子包是 {card['island']['offset_s']} s，它有一张卡在章节中间。"),
        ("影响", "只有 brief 要加黑底短卡时才用到，第 10 节写的是按需要才加。"),
        ("要你定", "按库拉岗日的样子留着就够了吗？要留的话，第 10 节可以写明这些值主要来自库拉岗日。"),
    ])
    l99 = J[m]["grade"]["look"]["L99"]
    kula99 = look["kula"]["L99"]["median"]
    inside = [key for key, *_ in LOOK if key != "L99" and all(
        abs(look[k][key]["median"] - J[m]["grade"]["look"][key]["median"]) <= J[m]["grade"]["look"][key]["mad"] for k, _, _ in VIDEOS)]
    if len(inside) != 7:
        FAILS.append(f"other look items no longer all fall inside the band: {inside}")
    f4 = judge("发现 4：L99 目标带不包住库拉岗日", ("复核发现", "found"), [
        ("问题", f"目标 {fx(l99['median'])} ± {fx(l99['mad'])}，上限 {fx(l99['median'] + l99['mad'])}，库拉岗日是 {fx(kula99)}，差 {fx(kula99 - l99['median'])}。<code>compare.py</code> 的容差取 max(3, MAD)，这里是 {fx(l99['mad'], 2)}，这一项过不了。其余 7 项三条都在目标带内。"),
        ("影响", "不大。第 9 节说调色核对用子包，不用这一层。只有拿博主级包核对明亮雪景时会报白位偏高。"),
        ("要你定", "L99 留着，还是也从博主级目标里去掉？按第 9 节的规则，相对差不超过 30% 就留，它符合这条规则。"),
    ])
    small = [
        f"开头一句写「约 3.5–4 秒一刀」，孤岛实际是 {fx(J['island']['rhythm']['shot_len_s']['median'], 2)} s。",
        f"第 11 节的 BPM 写 115、111，style.json 是 {J['island']['audio']['bpm']['median']}、{J['kula']['audio']['bpm']['median']}，只是四舍五入。合并值 {J[m]['audio']['bpm']['median']} 是三条的中位数，对得上。",
    ]
    quote(m, "pre", "约 3.5–4 秒一刀")
    small_html = "".join(f"<li>{inline(s)}</li>" for s in small)
    return (f'<section id="judge"><h2>待你判断</h2><p class="section-note">先看你列的四项，再看复核时发现的问题。都没有改源文件。</p>'
            f'<div class="judges">{a}{b}{c}{d}</div>{other_note}'
            f'<h3 class="sub">复核时发现的问题</h3><div class="judges">{f1}{f2}{f3}{f4}</div>'
            f'<h3 class="sub">小事，不影响判断</h3><ul class="plain">{small_html}</ul></section>')


def build_compare() -> str:
    keys = [(k, n) for k, n, _ in VIDEOS]
    m = J["merged"]

    def fig(fid, title, sec, lead, *body):
        return (f'<figure class="fig" id="{fid}"><figcaption><h3>{title}</h3><p>{lead} <a class="sec" href="#links-s{sec}">§{sec}</a></p></figcaption>'
                + "".join(body) + "</figure>")

    # rhythm
    rows = []
    for k, n in keys + [("merged", "合并后")]:
        s = J[k]["rhythm"]["shot_len_s"]
        chap = fx(section_of(k, "chapter")["shot_len_s"]["median"], 2) if k != "merged" else "—"
        spread = pct(J[k]["evidence"]["consistency"]["shot_len_s"]["spread"]) if k != "merged" else "—"
        rows.append([swatch(k, n) if k != "merged" else esc(n), fx(s["median"], 2), fx(s["mad"], 2), fx(s["p10"], 2), fx(s["p90"], 2),
                     fx(J[k]["rhythm"]["cuts_per_min"]), str(s["n"]), chap, spread])
    quote("merged", 4, "雪山占的分量最大")
    rhythm = fig("cmp-rhythm", "节奏", 4, f"三条的中位数只差 {pct(m['evidence']['consistency']['shot_len_s']['spread'])}，各章之间差得多。",
                 legend(), chart_rhythm(),
                 data_table(["视频", "中位数 s", "MAD", "P10", "P90", "每分钟剪辑点", "镜头数", "只算章节 s", "各章之间差"], rows),
                 '<p class="note">合并后的数是把所有镜头放在一起算的，雪山占的分量最大，比较风格时看每条的中位数。</p>')

    # transitions
    stat = "".join(f'<div><dt>{swatch(k, n)}</dt><dd>{pct(J[k]["transitions"][0]["share"])}</dd></div>' for k, n in keys)
    stat += f'<div><dt>合并后</dt><dd>{pct(m["transitions"][0]["share"])}</dd></div>'
    rows = []
    for k, n in keys:
        c = counts_of(k)
        rows.append([swatch(k, n), pct(J[k]["transitions"][0]["share"]), str(c["hard_cut"]), str(c.get("dip_to_black", 0)), str(frames_of(k, "dip_to_black")),
                     str(c.get("dissolve", 0)), str(frames_of(k, "dissolve")), str(c.get("other", 0))])
    tot = {t: sum(counts_of(k).get(t, 0) for k, _ in keys) for t in ("hard_cut", "dip_to_black", "dissolve", "other")}
    quote("merged", 6, f"{tot['dissolve']} 次里 {counts_of('snow')['dissolve']} 次来自雪山")
    rows.append(["合计 / 合并后", pct(m["transitions"][0]["share"]), str(tot["hard_cut"]), str(tot["dip_to_black"]), str(frames_of("merged", "dip_to_black")),
                 str(tot["dissolve"]), str(frames_of("merged", "dissolve")), str(tot["other"])])
    transitions = fig("cmp-transitions", "转场", 6, "硬切占绝大多数，所以占比单独写，下面只画其余转场。",
                      f'<dl class="stats"><div class="stats-h"><dt>硬切占比</dt></div>{stat}</dl>', legend(), chart_transitions(),
                      data_table(["视频", "硬切占比", "硬切", "黑场", "黑场帧数", "叠化", "叠化帧数", "手挡"], rows),
                      '<p class="note">孤岛的 2 次叠化判读都是 <code>sure: false</code>。雪山的黑场是 10 条标签、8 处，两处先淡出再淡入。手挡镜头已从 <code>transitions</code> 去掉，计数只在 evidence 里。</p>')
    quote("snow", 6, "两处黑场是先淡出到黑、再从黑里淡入")

    # shots
    rows = []
    for key, name in SHOTS:
        rows.append([esc(name)] + [pct(J[k]["selection"]["shot_types"].get(key, 0)) for k, _ in keys]
                    + [pct(m["selection"]["shot_types"][key]), "不一致" if "不一致" in row(3, name)[5] else "一致"])
    move = quote("merged", 3, "稳定前进或后退的镜头占 24.9%（孤岛）、20.8%（库拉岗日）、25.0%（雪山）")
    still = quote("merged", 3, "固定机位 27.2%、40.8%、32.5%")
    shots = fig("cmp-shots", "景别", 3, "中景最多，远景第二，三条都这样。只有航拍占比不一致。",
                legend(f'<span><i class="sw s-merged sw-line"></i>合并后</span>'), chart_shots(),
                data_table(["景别", "孤岛", "库拉岗日", "雪山", "合并后", "标注"], rows),
                f'<p class="note">每条各自的统计，不含地图、照片插入、黑镜头和碎镜头。机位：{move}；{still}。</p>')

    # grade
    rows = []
    for key, _, name, _, dec in LOOK:
        fmt = (lambda v: signed(v, dec)) if key == "sh_a" else (lambda v: fx(v, dec))
        t = m["grade"]["look"][key]
        rows.append([esc(name)] + [fmt(J[k]["grade"]["look"][key]["median"]) for k, _ in keys] + [f"{fmt(t['median'])} ± {fx(t['mad'], dec)}"])
    rows2 = []
    for key, name in REMOVED:
        vals = [J[k]["grade"]["look"][key]["median"] for k, _ in keys]
        rows2.append([esc(name)] + [signed(v) for v in vals] + [fx(max(vals) - min(vals))])
    grade = fig("cmp-grade", "调色", 9, "亮度、对比、饱和度接近，所以留下；色偏差得多，所以去掉。去掉的项由你判断，见上面的手改项。",
                '<h4 class="fig-sub">保留的 8 项</h4>', legend(f'<span><i class="sw s-merged sw-line"></i>合并后中位数</span><span><i class="sw sw-band"></i>目标带</span>'), chart_grade_kept(),
                data_table(["项", "孤岛", "库拉岗日", "雪山", "目标（合并后 ± MAD）"], rows),
                '<h4 class="fig-sub">去掉的 5 项</h4>', legend(), chart_grade_removed(),
                data_table(["项", "孤岛", "库拉岗日", "雪山", "最大差"], rows2),
                '<p class="note">图的最后一行暗部 a 是留下的，放在这里作对比，表里没有列。<code>hue_hist</code> 也去掉了，它是分布，这里没有画。</p>')

    # beat
    def line(name, fn):
        return [name] + [fn(k) for k, _ in keys]

    silent = row(5, "非人声时长")
    rows = [
        ["非人声时长"] + [esc(silent[i]) for i in (1, 2, 3)],
        line("里面的剪辑点", lambda k: str(beat_of(k)["n_cuts"])),
        line("落在拍点 ±1 帧", lambda k: pct(beat_of(k)["hit_rate"])),
        line("随机基线", lambda k: pct(beat_of(k)["baseline"])),
        line("是基线的几倍", lambda k: fx(beat_of(k)["lift"])),
        line("落在重拍上", lambda k: pct(beat_of(k)["downbeat_hit_rate"])),
        line("<code>beat_alignment</code>", lambda k: f'<code>{beat_of(k)["verdict"]}</code>'),
        line("BPM（beat_this，存疑）", lambda k: str(J[k]["audio"]["bpm"]["median"])),
        line("拍距折算 BPM（存疑）", lambda k: fx(beat_of(k)["bpm"])),
    ]
    beat = fig("cmp-beat", "卡点", 5, "三条都高于随机。过 30% 线的是两条李现的片子，孤岛没过，三条分不开原因。",
               legend(f'<span><i class="sw s-base"></i>随机基线</span>'), chart_beat(),
               data_table(["项", "孤岛", "库拉岗日", "雪山"], rows),
               f'<p class="note">{tag("存疑", "doubt")} 两种 BPM 对不上，命中率是按 beat_this 的拍点算的。合并后 <code>beat.hit_rate</code> 取中位数 {pct(m["beat"]["hit_rate"])}，plan_cut 按这个概率把剪辑点挪到拍点上。</p>')
    quote("merged", 5, f"合并后 `beat.hit_rate` 取中位数 {pct(m['beat']['hit_rate'])}")
    return f'<section id="compare"><h2>三条对照</h2>{rhythm}{transitions}{shots}{grade}{beat}</section>'


def build_sample() -> str:
    table = next(t for t in md_tables(SEC["merged"]["pre"]) if t[0][0] == "简称")
    longest = 0
    parsed = []
    for r in table[1:]:
        mm, ss = r[2].split(":")
        secs = int(mm) * 60 + int(ss)
        longest = max(longest, secs)
        parsed.append((r, secs))
    keys = {n: k for k, n, _ in VIDEOS}
    rows = []
    for r, secs in parsed:
        k = keys[r[0]]
        src = J[k]["sources"][0]
        if src["title"] not in r[1]:
            FAILS.append(f"sample table title does not match style.json: {r[1]}")
        rows.append(f'<div class="reel"><div class="reel-h"><strong>{esc(r[0])}</strong> <a href="{attr(src["url"])}">{esc(r[1])}</a></div>'
                    f'<div class="reel-bar"><i class="s-{k}" style="width:{secs / longest * 100:.1f}%"></i></div>'
                    f'<div class="reel-m">{esc(r[2])} · {esc(r[3])} 章 · {J[k]["rhythm"]["shot_len_s"]["n"]} 个镜头 · 测量 {esc(src["measured"])}</div></div>')
    pre = SEC["merged"]["pre"].split("**样本的局限，读结论时要记着：**")[1]
    bullets = [l[2:] for l in pre.splitlines() if l.startswith("- ")]
    lis = "".join(f"<li>{inline(b)}</li>" for b in bullets)
    return (f'<section id="limits" class="limits"><div><h2>样本的局限</h2><ul>{lis}</ul></div>'
            f'<div class="reels"><p class="reels-h">三条样本，长度按比例</p>{"".join(rows)}</div></section>')


def build_docs() -> str:
    links_html = md_to_html(MD["merged"], "links")
    snow_html = md_to_html(MD["snow"], "snow")
    check_full_text(MD["merged"], links_html, "links/STYLE.md")
    check_full_text(MD["snow"], snow_html, "first-snow-mountain/STYLE.md")
    links = (f'<article class="doc" id="doc-links"><p class="path"><a href="{REL}/STYLE.md">vlog-style/styles/links/STYLE.md</a> · '
             f'<a href="{REL}/style.json">style.json</a> · 博主级包，三条合并</p>{toc(MD["merged"], "links")}<div class="md">{links_html}</div></article>')
    snow = (f'<article class="doc" id="doc-snow"><p class="path"><a href="{REL}/first-snow-mountain/STYLE.md">vlog-style/styles/links/first-snow-mountain/STYLE.md</a> · '
            f'<a href="{REL}/first-snow-mountain/style.json">style.json</a> · 第三条子包，这里的「一致」指 14 章之间一致</p>{toc(MD["snow"], "snow")}<div class="md">{snow_html}</div></article>')
    return f'<section id="full"><h2>全文</h2><p class="section-note">两份 STYLE.md 的完整内容，由源文件生成，没有改动。</p>{links}{snow}</section>'


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()


CSS = r"""
/* 评审单：冷灰纸面上直接落图和表，只有待判断的条目和样本局限成块；宋体标题，系统黑体正文 */
:root{
  --bg:#f7f8f8;--ink:#14181c;--ink-2:#434b52;--ink-3:#626c75;--rule:#dde1e4;--rule-2:#b5bcc2;--wash:#eceff1;--band:#dfe4e8;
  --s-island:#1baf7a;--s-kula:#eb6834;--s-snow:#2a78d6;--s-base:#8b949c;
  --warn-bg:#fbefd0;--warn-fg:#6a4800;--warn-line:#d3a12a;
  --display:"Songti SC","STSong","Noto Serif CJK SC","Source Han Serif SC",serif;
  --body:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",system-ui,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#16191c;--ink:#e9ecee;--ink-2:#b9c1c8;--ink-3:#8f98a0;--rule:#2b3136;--rule-2:#4a535b;--wash:#20252a;--band:#2a3036;
  --s-island:#199e70;--s-kula:#d95926;--s-snow:#3987e5;--s-base:#7d868e;
  --warn-bg:#352a10;--warn-fg:#f0ca73;--warn-line:#7d6222;color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#16191c;--ink:#e9ecee;--ink-2:#b9c1c8;--ink-3:#8f98a0;--rule:#2b3136;--rule-2:#4a535b;--wash:#20252a;--band:#2a3036;
  --s-island:#199e70;--s-kula:#d95926;--s-snow:#3987e5;--s-base:#7d868e;
  --warn-bg:#352a10;--warn-fg:#f0ca73;--warn-line:#7d6222;color-scheme:dark}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{scroll-behavior:smooth}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 var(--body);-webkit-text-size-adjust:100%}
a{color:inherit;text-decoration-color:var(--rule-2);text-underline-offset:3px}
a:hover{text-decoration-color:currentColor}
a:focus-visible,.mark:focus-visible{outline:2px solid var(--s-snow);outline-offset:2px}
code{font:.88em var(--mono);background:var(--wash);padding:.08em .32em;border-radius:3px;overflow-wrap:anywhere}
h1,h2{font-family:var(--display);font-weight:700;letter-spacing:.01em;text-wrap:balance;line-height:1.3;margin:0}
h1{font-size:38px}
h2{font-size:26px}
h3{font-size:17px;line-height:1.45;margin:0;text-wrap:balance}
p{margin:0}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--rule);overflow-x:auto}
.bar-in{display:flex;gap:18px;white-space:nowrap;max-width:1080px;margin:auto;padding-block:9px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));font-size:13px;color:var(--ink-2)}
.bar-in strong{color:var(--ink);font-weight:600}
.bar a{text-decoration:none}
.bar a:hover{color:var(--ink);text-decoration:underline}
.wrap{max-width:1080px;margin:auto;padding-block:36px 72px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:56px}
.wrap>*{min-width:0}
section[id],article[id],figure[id],h4[id]{scroll-margin-top:56px}
.head{display:grid;gap:14px}
.eyebrow{font:12px var(--mono);letter-spacing:.04em;color:var(--ink-3)}
.lede{font-size:17px;line-height:1.8;max-width:46em}
.meta{font-size:13px;color:var(--ink-2)}
.limits{background:var(--warn-bg);border:1px solid var(--warn-line);padding:24px;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:28px}
.limits h2{font-size:22px;margin-bottom:10px}
.limits ul{margin:0;padding-left:1.2em;display:grid;gap:8px}
.reels{display:grid;gap:12px;align-content:start}
.reels-h{font-size:12px;color:var(--ink-2)}
.reel{display:grid;gap:3px;min-width:0}
.reel-h{font-size:13.5px;line-height:1.5}
.reel-bar{height:8px;background:transparent;border-bottom:1px solid var(--warn-line)}
.reel-bar i{display:block;height:7px;border-radius:0 3px 3px 0}
.reel-m{font-size:12px;color:var(--ink-2);font-variant-numeric:tabular-nums}
.section-note{color:var(--ink-2);margin-top:6px}
.tally{margin-block:10px 24px;color:var(--ink-2)}
.tally b{font-size:22px;color:var(--ink);font-weight:600}
.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:40px;margin-top:28px}
.cols>*{min-width:0}
.stack{display:grid;gap:28px;align-content:start}
.group h3{display:flex;align-items:center;gap:8px}
.group .n{font-size:13px;font-weight:400;color:var(--ink-3)}
.group-note{font-size:13px;color:var(--ink-2);margin-block:4px 8px}
.claims{list-style:none;margin:0;padding:0}
.claims li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;padding-block:7px;border-top:1px solid var(--rule);font-size:14px;line-height:1.65}
.sec{font:12px var(--mono);color:var(--ink-3);white-space:nowrap;text-decoration:none}
.sec:hover{color:var(--ink);text-decoration:underline}
.note{font-size:13px;color:var(--ink-2);margin-top:12px;max-width:62em}
.tag{display:inline-block;font:600 12px/1 var(--body);padding:3px 6px;border-radius:3px;border:1px solid transparent;white-space:nowrap;vertical-align:.1em}
.tag-ok{background:var(--ink);color:var(--bg)}
.tag-diff{border-color:var(--ink-2);color:var(--ink)}
.tag-one{border:1px dashed var(--ink-3);color:var(--ink-2)}
.tag-doubt{background:var(--warn-bg);color:var(--warn-fg);border-color:var(--warn-line)}
.tag-doubt::before{content:"? "}
.tag-two{background:var(--wash);color:var(--ink);border-color:var(--rule-2)}
.tag-edit{background:var(--wash);color:var(--ink);border-color:var(--rule-2);font-weight:400}
.tag-draft{background:var(--warn-bg);color:var(--warn-fg);border-color:var(--warn-line);font-weight:400}
.tag-found{border-color:var(--ink-2);color:var(--ink);font-weight:400}
.sub{font-size:18px;margin-top:36px}
.judges{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;margin-top:18px}
.judge{border:1px solid var(--rule-2);padding:18px;display:grid;gap:12px;align-content:start;min-width:0}
.judge dl{margin:0;display:grid;gap:8px}
.kv{display:grid;grid-template-columns:3.4em minmax(0,1fr);gap:8px;font-size:14px;line-height:1.7}
.kv dt{color:var(--ink-3);font-size:13px}
.kv dd{margin:0;min-width:0}
.kv.ask{background:var(--wash);padding:10px;margin-inline:-10px}
.kv.ask dt{color:var(--ink);font-weight:600}
.plain{margin:10px 0 0;padding-left:1.2em;font-size:14px;color:var(--ink-2);display:grid;gap:4px}
.fig{margin:36px 0 0;display:grid;gap:12px;min-width:0}
.fig+.fig{border-top:1px solid var(--rule);padding-top:32px}
.fig figcaption{display:grid;gap:4px}
.fig figcaption h3{font-family:var(--display);font-size:20px}
.fig-sub{margin:12px 0 0;font-size:14px}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:12.5px;color:var(--ink-2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:.02em}
.legend .sw{margin-right:0}
.s-island{background:var(--s-island)}.s-kula{background:var(--s-kula)}.s-snow{background:var(--s-snow)}.s-base{background:var(--s-base)}.s-merged{background:var(--ink)}
.sw-line{width:2px;height:13px;border-radius:0}
.sw-band{background:var(--band);border-radius:0;width:14px}
.chart{overflow-x:auto}
.chart svg{display:block;width:100%;min-width:680px;height:auto}
svg text{font-family:var(--body);font-size:12.5px;fill:var(--ink-2)}
svg .t{fill:var(--ink)}
svg .tm{font-size:11.5px;fill:var(--ink-3)}
svg .tv{font-weight:600;fill:var(--ink);font-variant-numeric:tabular-nums}
svg .tv2{font-size:12px;fill:var(--ink-2);font-variant-numeric:tabular-nums}
svg .tcode{font-family:var(--mono);font-size:11.5px;fill:var(--ink-2)}
.grid{stroke:var(--rule);stroke-width:1}
.axis{stroke:var(--rule-2);stroke-width:1}
.range{stroke:var(--rule-2);stroke-width:2;stroke-linecap:round}
.band{fill:var(--band)}
.hit{fill:transparent}
.mark{outline:none;cursor:default}
.mark:hover .hit,.mark:focus-visible .hit{fill:var(--wash)}
.mark:focus-visible .hit{stroke:var(--s-snow);stroke-width:1.5}
.dot{stroke:var(--bg);stroke-width:2}
.f-island{fill:var(--s-island)}.f-kula{fill:var(--s-kula)}.f-snow{fill:var(--s-snow)}.f-merged{fill:var(--ink)}.f-base{fill:var(--s-base)}
.r-island,.r-kula,.r-snow,.r-merged{opacity:.34}
.r-island{fill:var(--s-island)}.r-kula{fill:var(--s-kula)}.r-snow{fill:var(--s-snow)}.r-merged{fill:var(--ink)}
.l-island,.l-kula,.l-snow{stroke-width:2;opacity:.55}
.l-island{stroke:var(--s-island)}.l-kula{stroke:var(--s-kula)}.l-snow{stroke:var(--s-snow)}
.stats{display:flex;flex-wrap:wrap;gap:8px 36px;margin:0;align-items:flex-end}
.stats dt{font-size:12.5px;color:var(--ink-2)}
.stats dd{margin:0;font-size:26px;font-weight:600;line-height:1.3}
.stats-h{align-self:center}
.stats-h dt{font-weight:600;color:var(--ink);font-size:13px}
.tw{overflow-x:auto;min-width:0}
table{border-collapse:collapse;width:100%;font-size:13.5px;line-height:1.55}
th{font-weight:600;text-align:left;border-bottom:1px solid var(--rule-2);padding:6px 10px 6px 0;vertical-align:bottom}
td{border-bottom:1px solid var(--rule);padding:6px 10px 6px 0;vertical-align:top}
.data{font-variant-numeric:tabular-nums;font-size:13px}
.data th,.data td{white-space:nowrap}
.data td:not(:first-child),.data th:not(:first-child){text-align:right;padding-left:12px;padding-right:0}
.chip{display:inline-block;width:11px;height:11px;border:1px solid var(--rule-2);margin-right:5px;vertical-align:-.05em}
.doc{margin-top:32px;display:grid;gap:14px;min-width:0}
.doc+.doc{margin-top:64px;border-top:1px solid var(--rule-2);padding-top:32px}
.path{font:12px/1.6 var(--mono);color:var(--ink-3);overflow-wrap:anywhere}
.toc{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;color:var(--ink-2)}
.toc a{text-decoration:none;white-space:nowrap}
.toc a:hover{color:var(--ink);text-decoration:underline}
.md{display:grid;gap:12px;min-width:0}
.md>*{min-width:0}
.md p,.md ul,.md ol{max-width:48em;margin:0}
.md ul,.md ol{padding-left:1.2em;display:grid;gap:4px}
.md ul ul,.md ul ol{margin-top:4px}
.md .doc-title{font-family:var(--display);font-size:22px;margin:0}
.md h4{font-size:17px;margin:22px 0 0;padding-top:12px;border-top:1px solid var(--rule)}
.foot{border-top:1px solid var(--rule-2);padding-top:18px;font-size:13px;color:var(--ink-2);display:grid;gap:6px}
.foot code{font-size:.92em}
#tip{position:fixed;z-index:9;left:0;top:0;pointer-events:none;background:var(--ink);color:var(--bg);font-size:12.5px;line-height:1.55;padding:7px 10px;border-radius:4px;max-width:300px}
.tip-h{font-weight:600}
@media (max-width:860px){
  h1{font-size:30px}
  .limits,.cols,.judges{grid-template-columns:minmax(0,1fr)}
  .limits{padding:18px}
}
@media (forced-colors:active){.hit{fill:none}}
"""

JS = r"""
(function () {
  var tip = document.getElementById('tip');
  function fill(el) {
    tip.textContent = '';
    el.dataset.tip.split('\n').forEach(function (line, i) {
      var row = document.createElement('div');
      row.textContent = line;
      if (i === 0) row.className = 'tip-h';
      tip.appendChild(row);
    });
    tip.hidden = false;
  }
  function place(x, y) {
    var r = tip.getBoundingClientRect();
    var top = y + 14 + r.height > innerHeight ? y - r.height - 10 : y + 14;
    tip.style.left = Math.max(8, Math.min(x + 14, innerWidth - r.width - 8)) + 'px';
    tip.style.top = Math.max(8, top) + 'px';
  }
  document.addEventListener('pointermove', function (e) {
    var m = e.target.closest ? e.target.closest('.mark') : null;
    if (!m) { tip.hidden = true; return; }
    fill(m);
    place(e.clientX, e.clientY);
  });
  document.addEventListener('focusin', function (e) {
    if (!e.target.classList || !e.target.classList.contains('mark')) return;
    fill(e.target);
    var r = e.target.getBoundingClientRect();
    place(r.left + 12, r.top + r.height / 2);
  });
  document.addEventListener('focusout', function () { tip.hidden = true; });
  document.addEventListener('scroll', function () { tip.hidden = true; }, { passive: true, capture: true });
})();
"""


def main() -> None:
    check_numbers()
    sample = build_sample()
    verdict = build_conclusions()
    judgments = build_judgments()
    compare = build_compare()
    docs = build_docs()
    branch, commit = git("branch", "--show-current"), git("rev-parse", "--short", "HEAD")
    dirty = git("status", "--porcelain", "--", "vlog-style/styles/links")
    state = "，links 目录有未提交改动" if dirty else ""
    oneline = re.search(r"^一句话：(.+)$", SEC["merged"]["pre"], re.M)[1]
    subpacks = "".join(f'<li><a href="{REL}/{d}/STYLE.md">{d}/STYLE.md</a>（{n}，已确认，不嵌入全文）</li>' for k, n, d in VIDEOS if k != "snow")
    head = (f'<header class="head"><p class="eyebrow">vlog-style · styles/links</p><h1>Links 风格专辑审阅</h1>'
            f'<p class="lede">{inline(oneline)}</p>'
            f'<p class="meta">分支 <code>{esc(branch)}</code> · 提交 <code>{esc(commit)}</code>{state} · 被审的是 '
            f'<a href="#doc-links">links/STYLE.md</a>（博主级，三条合并）和 <a href="#doc-snow">first-snow-mountain/STYLE.md</a>（第三条子包）</p></header>')
    nav = ('<nav class="bar" aria-label="页面导航"><div class="bar-in"><strong>Links 风格专辑审阅</strong><a href="#limits">局限</a><a href="#verdict">结论</a>'
           '<a href="#judge">待你判断</a><a href="#compare">三条对照</a><a href="#doc-links">全文·博主级</a><a href="#doc-snow">全文·雪山</a></div></nav>')
    placeholder = "@@CHECKS@@"
    foot = (f'<footer class="foot"><p>页面只用这些文件的文字和数字：<code>vlog-style/styles/links/</code> 下的 STYLE.md 和 style.json，以及三个子包的 STYLE.md 和 style.json。'
            f'没有读 stills/ 和 refs/，没有参考片画面，没有台词。</p><p>对照用的子包：</p><ul class="plain">{subpacks}</ul>'
            f'<p>{placeholder}</p><p>源文件改了之后重新生成：<code>python3 showcase/links-review/build.py</code></p></footer>')
    page = ('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f'<title>Links 风格专辑审阅</title><style>{CSS}</style></head><body>{nav}<main class="wrap">{head}{sample}{verdict}{judgments}{compare}{docs}{foot}</main>'
            f'<div id="tip" role="status" hidden></div><script>{JS}</script></body></html>\n')

    for bad, why in (("{{", "placeholder"), ("<img", "image"), ("<link", "external stylesheet"), ("<script src", "external script"), ("@import", "external css"), ("<iframe", "embed")):
        if bad in page:
            FAILS.append(f"page contains {why}: {bad}")
    for k, _, _ in VIDEOS:
        if BV[k] not in page:
            FAILS.append(f"page lost the link for {k}")
    if FAILS:
        print(f"{len(FAILS)} check(s) failed:", file=sys.stderr)
        for f in FAILS:
            print(" -", f, file=sys.stderr)
        sys.exit(1)
    summary = (f"构建时核对通过：{COUNT['numbers']} 个 STYLE.md 表格数字和 style.json 一致，{COUNT['quotes']} 处引用在所引小节里找得到，"
               f"两份全文 {COUNT['lines']} 个文本片段都在页面里。")
    (HERE / "index.html").write_text(page.replace(placeholder, summary), encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()

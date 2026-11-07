# 风格包格式

一个风格包在仓库里是 `vlog-style/styles/<名字>/` 下的三个文件：

| 文件 | 给谁看 | 内容 |
|---|---|---|
| `STYLE.md` | 人和 agent | 按做片顺序写的完整流程，每条都有数值、依据和一致性标注 |
| `style.json` | plan_cut.py、compare.py | 同一套流程的机器参数，`schema.py style` 校验通过才算数 |
| `sources.md` | 人 | 开头是出处表（模板见下）：参考片链接、UP 主、发布日期、时长、测量日期；然后写用了哪些章节。参考片本身不保留，测量 keep 在 root 的 `refs/` 下，需要重测时按链接重新下 |

派生素材不进仓库，放在 `<root>/styles/<名字>/`：`stills/`（不超过 10 张低清关键帧，用于验收对照）、`luts/`、`drx/`、`fusion/`。style.json 里引用它们时写相对风格目录的路径，比如 `"drx": "drx/look.drx"`。

## 两种文件夹：博主专辑和 misc

```
vlog-style/styles/                    仓库
  links/                              博主专辑
    sources.md                        出处表 + 子包表 + 候选视频表
    STYLE.md, style.json              博主级合并包，炼完 ≥2 条才写（basis = multi_reference）
    polar-night/                      子包：STYLE.md, style.json, sources.md
  misc/                               只要这一条的风格，顶层没有合并包
    README.md
    <名字>/                           STYLE.md, style.json, sources.md

<root>/styles/                        本机
  links/refs/<BV号>/                  keep 留下的测量
  links/polar-night/stills/ …
  misc/refs/<id>/
  misc/<名字>/stills/ …
```

- **调用。** `--style links`（博主级）、`--style links/polar-night`、`--style misc/<名字>`。只允许一层嵌套，子包不能叫 `refs`、`stills`、`luts`、`drx`、`fusion`。找不到包时报错并列出已有的包，比如还没有博主级包时 `--style links` 会列出 `links/` 下的子包。
- **refs/。** `cleanup.py keep` 写进去：measure_video.json、measure_audio.json（转写和字幕原文剥掉，标题文字保留，labels 要用）、labels.json、meta.json（只留 bvid、title、author、mid、publish_time、duration_sec）。够重新 draft，不够重跑 measure。只在本机，不进仓库。
- **博主专辑只收本人投稿的视频。** 入池前核对 mid；别人的视频即使出现在投稿列表里，也放进 misc，出处写真实的 UP 主。
- **博主级包按每条视频比较**（multi_reference）。节奏写各条视频中位数的分布。调色跟着内容走，博主级包只写各条一致的部分，调色核对用子包。

## sources.md 模板

每个包都要有，至少要有视频链接。出处主要给人回溯；复刻时默认只靠风格包本身，什么时候回看原片见 SKILL.md 第 5 节。

子包和 misc 包：

```markdown
# <包名> 的参考来源

| 项 | 内容 |
|---|---|
| 参考 id | <BV号或本地文件名> |
| 链接 | https://www.bilibili.com/video/<BV号>（本地文件写「本地文件」） |
| 标题 | <视频标题> |
| UP 主 | <名字>（mid <mid>，https://space.bilibili.com/<mid>） |
| 发布日期 | YYYY-MM-DD |
| 时长 | mm:ss |
| 测量日期 | YYYY-MM-DD |
| 用到的部分 | 全片或哪几章；没算进来的段落和原因 |

## 怎么测的
## 保留了什么
仓库里的三个文件；`<root>/styles/<博主|misc>/refs/<id>/` 有没有 keep；stills/ 几张。
## 后续
回看补充、搬家、重测，每条带日期。
```

博主文件夹的 sources.md：出处表写 UP 主、空间链接和入池视频（每条一行）；子包表写每个子包出自哪条视频；候选视频表列出看过的投稿：

```markdown
| 状态 | 标题 | 链接 | 发布 | 时长 | UP 主 | 理由 |
|---|---|---|---|---|---|---|
| 入池 / 待炼 / 不入池 | … | https://www.bilibili.com/video/<BV号> | YYYY-MM-DD | mm:ss | <名字>（mid <mid>） | 为什么入池或不入池 |
```

风格包只放「怎么剪」，不放参考片的画面、音乐、字体，也不放某一次复刻的内容。

## style.json 字段

机器契约在 `scripts/schema.py` 的 `STYLE`。`aggregate.py draft` 生成草稿，agent 审过、补完再定稿。

| 字段 | 含义 | 来源 |
|---|---|---|
| `schema_version` | 固定为 1 | — |
| `name`、`summary` | 名字和一句话风格 | summary 由 agent 写 |
| `sources[]` | `{id, url, title, measured, author}`。url、title 必填：B 站视频的 url 是视频链接，本地文件 url 留空、title 用文件名；author 可选 | aggregate 自动填：measured 取 measure_video.json 测量时写下的日期（旧文件没有才用当天），title、author、url 取参考目录里的 meta.json |
| `basis` | `multi_reference`（多个参考之间比较）或 `single_reference_chapters`（一个参考的各章之间比较） | 参考个数 |
| `deliverable` | `fps`、`width`、`height`、`duration_s`（分布）、`letterbox`（遮幅宽高比，没有为 null） | 参考片的帧率；尺寸默认 3840×2160，按用户素材定 |
| `structure.sections[]` | `{role, share, shot_len_s, transition_in, notes}`。role 取 hook / intro / chapter / interlude / ending；share 之和为 1；同一个 role 只写一条，plan_cut 会把 chapter 按 brief 的章节展开 | labels 的章节 + 测量 |
| `structure.order`、`structure.order_tolerance_s` | 章内选镜头按不按拍摄时间：`free`（不看时间）或 `chronological`。不写时，有 story 就按 `chronological`，没有 story 就是 `free`；只有用户明确要混剪才写 `free`。`order_tolerance_s` 是同一场景里允许往回调换的秒数，默认 300 | 手填，炼化不测。`chronological` 要求索引里有拍摄时间 |
| `selection` | `shot_types`（景别配比，和为 1）、`opening`、`reject[]`、`max_shake`（抖动 RMS 占画面宽的 %） | labels + 测量（max_shake 取参考的 P90 × 1.2） |
| `rhythm` | `shot_len_s`（分布）、`cuts_per_min`、`min_shot_s` | 测量 |
| `beat` | `hit_rate`、`baseline`（随机平移剪辑点后的命中率）、`offset_frames`（剪辑点相对拍点提前为负）、`use_downbeats`、`confidence`（ok / uncertain） | 测量。uncertain 时 plan_cut 不卡点 |
| `transitions[]` | `{type, share, frames, where, direction}`。type 取 `schema.TRANSITIONS`；share 之和为 1；where 取 any / within_section / section_boundary / chapter_boundary | labels 优先，没标的按测量计入并列为未核实 |
| `motion` | `push_in` / `pull_out`：`{share, scale_end}`，指剪辑里加的数字推拉；`slow_mo`：`{share, speed}`；`ramp`：`{share}`；`shake`：`{share, amplitude_px}` | 测量 + labels。推拉比例 = 测到的稳定推拉占比 × labels 里判为 digital 的比例；慢动作只能靠看图判断 |
| `effects[]` | `{type, ...}`，如颗粒、光晕、漏光、暗角 | labels |
| `grade` | `summary`、`look`（见下）、`lut`、`drx`、`grain` | 测量 + labels |
| `text` | `title`、`chapter_card`、`subtitle`，每项见下 | 测量 + labels |
| `audio` | `bpm`（分布）、`mood`、`lufs`、`duck_db`、`music_changes`、`sfx[]`、`broll_ambient_db`（B-roll 环境声放 A4 的电平，dB；不写就不放） | 测量；mood 和 sfx 由 agent 根据测量写；broll_ambient_db 手填 |
| `photo` | 照片怎么放：`dur_s`（每张几秒）、`scale`（相对铺满画面的大小，搭建时用 `set_transform` 设，1 是铺满）、`background`（目前只有 black）。不写时 3 s、1.0、black。只在 brief 写了 `photos` 时起作用 | 看参考里的照片插入：时长按镜头算，大小量照片在画面里的宽高 |
| `aroll` | 有 story 时 A-roll 怎么剪：`speech_share`（A-roll 占全片的比例，brief 不写 `duration_s` 时用它推成片时长）、`pad_s` / `pad_out_s`（句前、句后留白，按 whisper 的时间）、`cutaway_after_s`（多长的 A-roll 才盖画面）、`cutaway_share`（盖住的比例）、`cutaway_lead_s` / `cutaway_tail_s`（开头、结尾留给说话人的秒数）、`j_s` / `l_s`（J/L cut 声音提前、延后的秒数）。不写的项用 plan_cut 的默认值 | speech_share 取参考的人声占比；其余要按说话段测，测不了的标存疑 |
| `resolve[]` | `{element, route, status, how}`：每个用到的元素走哪条路线。route 取 `schema.ROUTES`，status 取 verified / probe / manual | aggregate 的路线表；探针后更新 |
| `evidence` | `consistency`（每项的一致性判断）、`unverified`、`rejected_labels`、`single_source`、`beat`、`ramp_candidates`、`onsets_per_min`、`moves`（推拉的测到 / 判过 / digital 个数） | aggregate 自动写 |

所有分布（DIST）的格式都是 `{median, mad, p10, p90, n}`，至少要有 median。

**grade.look** 是 Lab 数值的目标，每项都是 `{median, mad}`：

- `L01` / `L50` / `L99`：黑位、中间亮度、白位
- `Lstd`：对比度
- `shadow`：L<20 的像素占比
- `chroma`：饱和度
- `sh_a` / `sh_b`、`mid_a` / `mid_b`、`hi_a` / `hi_b`：暗部、中间调、高光三个亮度区的色偏（a 正为品红、负为绿；b 正为黄、负为蓝）
- `vignette`：四角亮度和中心亮度之比
- `hue_hist`：12 格色相分布

**文字样式**（TEXT_STYLE）的字段：

- `font_class`：黑体 / 宋体 / 圆体 / 楷体 / 手写 / 装饰体
- `fallback_fonts`：本机已装的备选字体
- `height_frac`：字高占画面高的比例
- `position`：文字中心，[x, y]，0–1，原点在左上
- `color`：[r, g, b]，0–1
- `stroke`、`plate`：有没有描边、有没有底板
- `in` / `out`：出入场方式
- `duration_s`、`offset_s`：持续多久，章节开始后多久出现
- `template`：Resolve 标题模板
- `per`：多久出现一次，取 chapter / location / section / never

## STYLE.md 模板

开头写风格名、一句话概括、依据（几个参考、哪几章），然后按下面 12 节写。每条都写数值和依据，并标注一致性：

- **一致**：几个参考或各章都这样，算作风格，复刻时照做。
- **不一致**：各不相同，复刻时可以自由发挥，写明范围。
- **单一来源**：只出现在一个参考或少数章节，待补参考后再定。
- **存疑**：测量或判读不可靠，写明原因。

```markdown
# <风格名>

一句话：……
依据：<参考片>，<章节数> 章，测量于 <日期>。basis = single_reference_chapters
出处：见 sources.md

## 1. 成片规格
画幅、分辨率、交付帧率、时长范围、遮幅。

## 2. 结构
开头钩子 / 片头 / 分章 / 过渡段 / 结尾，各段时长占比、每段从什么转场进入、各段的作用。

## 3. 选素材
景别配比、开场用什么镜头、哪些镜头不用、抖动上限、曝光要求。

## 4. 节奏
全片和各段的镜头时长（中位数 ± MAD，P10–P90）、每分钟剪辑点数、最短镜头。

## 5. 卡点
命中率 vs 随机基线、剪辑点提前或滞后几帧、是否卡重拍、J/L cut；人声多时写明「存疑」。

## 6. 转场
每种转场的占比、帧数、用在哪里（段内 / 段落边界 / 章节边界）、方向。

## 7. 运动与变速
推拉的占比和缩放幅度、慢动作的占比和速度、变速曲线、抖动。推拉要分开写：拍摄时的机位运动（无人机、车、步行）是选素材的偏好，写进第 3 节；剪辑里加的数字推拉才写进这里的比例。

## 8. 特效
颗粒、光晕、漏光、暗角等：有没有、多强、用在哪。

## 9. 调色
一句话基调，然后是 look card 表格（目标值和容差），对应的 LUT / CDL / DRX，以及怎么用 compare.py look 核对。

## 10. 文字
地点标题、章节卡、字幕：字体类别和备选字体、字高、位置和安全边距、颜色和描边、底板、出入场、时长、出现时机。

## 11. 声音
BPM、情绪、音乐和章节的对应、换曲点、环境声、旁白、音乐压低幅度、整体响度、音效。

## 12. Resolve 实施清单
每个元素走哪条路线、本机状态（已验证 / 待探针 / 手动），以及手动收尾清单。
```

写 STYLE.md 时只引用测量和 labels 里有的东西。evidence 里的 `unverified`、`rejected_labels` 和 `single_source` 都要在相应小节里交代。

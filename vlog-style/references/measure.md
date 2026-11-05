# 测量与判读

参考片的分析只用本地测量，再由 Claude 看抽帧拼图和事件条判读。参考片和用户素材都不上传到任何视频理解服务。时间一律以本地测量为准，Claude 的标签只负责补上类型和描述。

## 顺序和命令

下面的命令都在 `vlog-style/scripts/` 下运行。`R` 代表参考片的目录：

```sh
R=$(python3 -I -B paths.py resolve cache/refs/<id>)
```

| 步 | 命令 | 产出（在 `R` 下） |
|---|---|---|
| 1. 取片和代理 | `python3 -I -B prep.py ref <BV号或本地文件> --id <id>` | `src/`（下载的原片）、`meta.json`、`subtitles.json`（B 站有字幕时）、`proxy.mp4`、`asr16k.wav`、`music44k.wav`、`prep.json` |
| 2. OCR | `xcrun swift vision_ocr.swift "$R/src/<原片>" --fps 4 --out "$R/ocr.json"` | `ocr.json` |
| 3. 画面 | `uv run --python 3.13 --script measure_video.py "$R"` | `measure_video.json`、`frames/`、`strips/`、`sheets/`、`cuts_raw.npz`、`scan_raw.npz` |
| 4. 声音 | `uv run --python 3.13 --script measure_audio.py "$R" --prompt "<地名、人名>"` | `measure_audio.json` |
| 5. 字幕分类重算 | `uv run --python 3.13 --script measure_video.py "$R" --redo text` | 用人声段重新给文字分类，look 和 strips 会跟着重算 |
| 6. 标签骨架 | `python3 -I -B aggregate.py labels "$R"` | `labels.json` |
| 7. 汇总 | `python3 -I -B aggregate.py draft --name <风格名> "$R1" "$R2" … --out <风格包>/style.json` | 通过 schema 校验的 style.json 草稿。`R` 也可以是 keep 留下的 `styles/<博主\|misc>/refs/<id>`。`--out` 已存在时拒绝覆盖，加 `--force` 才覆盖；不加 `--out` 写到 `$R1/style.draft.json`。参考来自多个 mid 时打印警告 |
| 8. 留测量 | `python3 -I -B cleanup.py keep <id> --style <博主\|misc> [--yes]` | 不在 `R` 下，在 `<root>/styles/<博主\|misc>/refs/<id>/`：剥掉转写和字幕原文的 measure_video.json、measure_audio.json，labels.json，只留 6 个字段的 meta.json。之后才能 `cleanup.py trash <id>` |

几点说明：
- **OCR 跑在原片上，不跑代理。** 540p 下字幕只有 20 像素左右高，中文识别不可靠。OCR 默认把原片缩到 1080 高再识别。
- **先画面后声音。** 声音那一步要从画面结果里取烧录字幕，当作人声段，并优先用作转写。第 5 步再拿人声段回去，帮助给文字分类。
- **第一次运行会下载模型。**
  - torch 等依赖约 2 GB，已经下好。
  - beat_this 的权重 81 MB。服务器很慢，下载会被截断，所以不由脚本自动下载，要手动下到 `~/.cache/torch/hub/checkpoints/beat_this-final0.ckpt`。没下好时脚本自动改用 librosa，`beats.engine` 会记为 `librosa`，这时没有重拍。
  - whisper large-v3-turbo 约 1.6 GB，第一次转写时下载。没有人声的片子加 `--no-asr`。
- **在合成测试片上实测：** 16 秒的片子跑完全部测量约 10 秒。长片的耗时在第一次真实炼化时记到这里。博主模式的第一批（links 是 29–55 分钟）每步跑完都记一下耗时，再决定更长的片子要不要入池。
- **先 keep 再 trash。** 留下的测量够重新 draft（结果和用 cache 跑的一样），但没有原片、代理和抽帧，不能重跑 measure 的各阶段。`measure_video.json` 的 `measured` 记的是每次跑的日期；旧文件没有这一项，keep 时用文件修改日期补上。

## measure_video.py

可以只重跑某几个阶段（`--redo cuts,scan`），上游重跑后，依赖它的阶段会自动重跑。阈值都写在脚本顶部的常量里，调整之后要重跑合成测试。

| 阶段 | 做法 | 关键输出 |
|---|---|---|
| cuts | TransNetV2（CPU）跑 48×27 小图，概率超过 0.5 的连续帧算一个转场。连续不超过 3 帧算硬切，用帧差把剪辑点精确到具体一帧；更长的算渐变，剪辑点放在中点。再用 PySceneDetect AdaptiveDetector 交叉核对：只有 PySceneDetect 发现的剪辑点，需要 TransNet 至少有 0.1 的弱信号，或帧差突跳，才收；不收的记进 `psd_rejected` | `cuts[]`：frame、t、kind（hard / gradual）、span、frames、src（transnet / psd / both）。`shots[]` |
| text | 把 OCR 结果串成轨迹，再分成字幕、标题、其他三类（stats.link_text / classify_text） | `tracks[]`、`subtitle_band`、`transcript`（字幕文本） |
| scan | 按 320 宽逐帧扫一遍，记录：亮度、清晰度（拉普拉斯方差）、过曝和死黑比例、LK 光流仿射（缩放、平移）。由此得到遮幅、每个镜头的运动，以及转场和特效的候选。闪白和黑场下的剪辑点会移到闪白或黑场的中点（`snap`），因为那里看不出画面在哪一帧切换；每次都从检测器给的原始剪辑点（`cuts[].raw`）算起，重跑 scan 不会叠加 | `letterbox`、`shots[]`（motion.type / zoom / zoom_eff / pan / shake、exposure_ok、best、ramp_candidate、freeze）、`events[]`、`keyframes[]` |
| look | 从每个镜头的关键帧测 Lab 数值，测之前遮掉遮幅、字幕带和 OCR 文字框 | `summary`（每项的中位数和 MAD）、`hue_hist`、`per_shot[]` |
| strips | 在每个候选事件前后各取 0.5 秒，按原帧率拼成事件条。另外为每次标题出入场、每章抽几个硬切也各拼一条。标题颜色从框内像素测。推拉镜头最多抽 24 个，各取 10%、50%、90% 三帧拼成 `strips/moves_*.jpg`，给 Claude 判断是剪辑加的还是机位在动 | `events[].strip`、`titles[]`（含 colour：fill、edge）、`moves[]` |
| sheets | 用关键帧拼成总览图，每张 6×8 格。10 分钟以内的片子每个镜头取 3 张，更长的取 1 张 | `sheets[]` |

运动类型的判定阈值：
- `push_in` / `pull_out`：镜头内总缩放 ≥1.04 或 ≤1/1.04，并且是一个方向的稳定变化：`zoom_eff` ≥0.95。`zoom_eff` 是每 0.5 秒一段的对数缩放，净值除以各段绝对值之和。人从画面走过、转身、视差，都会让缩放来回变，`zoom_eff` 就低。
- `pan_*` / `tilt_*`：每秒移动 ≥0.04 个画面宽。
- `complex`：总缩放过了 1.04，但不稳定、也不是摇或俯仰。多是人或车在画面里动，或者机位边走边转。
- `handheld`：抖动 RMS ≥0.12% 画面宽。

`push_in` / `pull_out` 只说明画面在稳定地放大或缩小，不区分原因。无人机前飞、车载、步行、拿着东西往镜头凑，都会测成推拉；剪辑里加的数字推拉也是。plan_cut 只会给静止镜头加数字推拉，所以风格包里的推拉比例只按 Claude 判为 `digital` 的算（见判读清单第 2 步）。

`shake` 的单位是「抖动 RMS 占画面宽的百分比」，`selection.max_shake` 用的是同一个单位。

转场候选的判定：
- `flash_white`：亮度比剪辑点两侧的基线高 0.2 以上，且峰值高于 0.6。
- `dip_to_black`：最低亮度低于 0.06，且基线高于 0.12。
- 闪白和黑场的跨度都取到偏离量的 20% 处，而且两头都要在剪辑点前后 0.4 秒内回到基线。一直黑到窗口边上的，是硬切进了一个暗镜头（极夜、夜景），不算黑场；一直亮到边上的是切进了雪景，不算闪白。黑底字卡超过 0.4 秒的，也是两个硬切夹一个黑镜头，在标题里记成章节卡。
- `whip`：在剪辑点附近、转场范围外的帧里，清晰度掉到镜头中位数的 0.4 以下，同时平移超过 0.03 个画面宽，或者光流追踪失败 ≥3 帧。
- `zoom`：剪辑点附近有一帧的缩放超过 4%。

## measure_audio.py

| 字段 | 来源 |
|---|---|
| `loudness` | ffmpeg ebur128：整体响度（LUFS）、响度范围（LRA）、真峰值 |
| `speech`、`speech_share` | 字幕（measure_video）、B 站字幕、whisper 三个来源的时间段合并，间隔不超过 0.3 秒的连成一段 |
| `transcript` | 有烧录字幕就用字幕，没有就用 B 站字幕，再没有才用 whisper |
| `beats` | 用 beat_this 跑整条音轨，BPM 只按非人声段统计（人声边缘各让开 0.5 秒，只算 4 秒以上的段）。非人声段合计少于 30 秒且不到全片 25% 时，`confidence` 记为 `uncertain` |
| `tempo_check` | 用 librosa 的 BPM 交叉核对，用来发现 beat_this 把节拍测成一半或两倍的情况 |
| `onsets` | librosa 的 onset。强度在前 5%、又不在拍点上的，算音效候选 |
| `music` | 静音段、拍点中断超过 2.5 秒的地方、速度变化，都算换曲候选 |
| `duck` | 比较人声段内外 30–90 Hz 频段的电平差。这是估算，没有低音的混音会被误读成压得很深 |

BPM 的算法：先取拍点间隔的中位数，再对落在中位数 ±15% 以内的间隔取平均。beat tracker 输出的时间点会对齐到 20–23 ms 的帧格子上，只取中位数会被这个格子带偏，比如 120 BPM 会被测成 117.6。

给用户的音乐生成拍点网格：

```sh
uv run --python 3.13 --script measure_audio.py --music <音乐文件> --out <项目>/index/beats.json
```

## Claude 判读清单

Claude 听不到声音，声音相关的结论只能来自测量和转写。看图时，顺序如下：

1. **总览图（sheets）+ 字幕。** 划分结构：开头钩子、片头、按地点分章、过渡段、结尾。看每章的开场镜头是什么样的。镜头标签里，`P+`/`P-` 是推和拉，`<`/`>` 是摇，`^`/`v` 是俯仰，`Z~` 是不稳定的缩放，`~` 是手持。
2. **推拉镜头（strips/moves_*.jpg）。** labels.json 的 `shots` 里预填了抽样的推拉镜头，逐个填 `move`：
   - `digital`：整幅等比放大或缩小，前景和背景之间没有视差，遮挡关系不变。剪辑里加的推拉、变焦都算。
   - `camera`：机位真的在动（无人机、车、步行、稳定器），或者是被摄物朝镜头移动。前景比背景放大得快，遮挡关系在变。
   - 高空航拍前飞几乎没有视差，和数字推镜很像，这种 `sure` 填 false。
3. **转场事件条**（不是硬切的那些），逐条看：
   - 判断类型，取值为 `schema.TRANSITIONS` 之一；看错的候选标 `none`。
   - 记下方向（甩镜往哪边甩）和实际帧数。
   - 看不准的，`sure` 填 false。
   - 运动中切（镜头自己在甩、在转，切到下一个镜头）不是加的甩镜转场，标 `none`。真的甩镜转场，剪辑点两侧都有运动模糊。
4. **标题事件条（title_in / title_out）。**
   - `role`：location（地点标题）、chapter_card（章节卡）、title，或者 other（不是标题）。
   - 拍到的招牌、包装、商品上的字也会被分成标题，标 other。它们通常会随镜头漂移（`drift` 大），也常出现在画面边缘；叠加上去的标题位置不动。
   - 出入场动画：fade、slide_up、typewriter、scale、cut 等，用中性的话描述。
   - 字体只判断类别：黑体、宋体、圆体、楷体、手写、装饰体。
   - 有没有底板（plate）。
5. **抽样的硬切。** 确认它们确实是硬切；有没有 J/L cut，要结合字幕时间判断。
6. **全片特效。**
   - 颗粒（平台二次压缩之后测不准，只能看图判断：无 / 轻 / 中 / 重）。
   - 光晕、漏光、暗角、遮幅。
7. **调色。** 一句话概括基调。数值以 look 的测量为准，Claude 只写概括，不改数值。
8. **选镜头规律。** 景别配比（`shots[]` 里标 shot_type）、开场用什么镜头、哪类镜头从来不用。

## labels.json

由 `aggregate.py labels` 生成骨架，Claude 只填空格。例子：

```json
{
 "chapters": [{"name": "特罗姆瑟", "role": "chapter", "t0": 42.0, "t1": 410.5}],
 "events": [{"i": 3, "type": "dissolve", "frames": 12, "direction": null, "sure": true},
            {"i": 7, "type": "none", "sure": true}],
 "titles": [{"track": 12, "role": "location", "font_class": "宋体", "in": "fade", "out": "fade", "plate": false}],
 "shots": [{"i": 40, "shot_type": "wide", "slow_mo": true, "speed": 0.5},
           {"i": 88, "sheet": "strips/moves_01.jpg", "type": "push_in", "zoom": 1.56, "move": "camera", "sure": true}],
 "effects": [{"type": "grain", "global": true, "strength": "light"}, {"type": "light_leak", "event": 9}],
 "grade": {"summary": "冷蓝暗调，高光偏暖", "grain": "light"},
 "audio": {"mood": "舒缓钢琴 + 环境声", "sfx": [{"type": "whoosh", "event": 9}]},
 "selection": {"opening": "航拍远景", "reject": ["人物正脸特写"]}
}
```

对账规则（`aggregate.py` 执行）：
- 章节的 role 必须是 hook、intro、chapter、interlude、ending 之一，时间必须在片长之内且不能倒退。
- 事件、标题、镜头、特效都必须指向测到的序号（event `i`、title `track`、shot `i`），或者标为 `global`。
- 对不上的标签写进 `evidence.rejected_labels`，不会被悄悄用掉。
- 测到了但 Claude 没标的转场，按测量值计入，同时列进 `evidence.unverified`。
- 镜头的 `move` 只能是 digital 或 camera。推拉比例 = 测到的推拉占比 × 判过的里面 digital 的比例；一个都没判时按全部算，同时记进 `evidence.unverified`。判断的计数在 `evidence.moves`。

## 已知限制

- 代理是 540p，加上 B 站二次压缩，颗粒和色带都不可信，只看大的色调。
- Vision OCR 会漏字，比如把「远处的山」读成「远处朐山」，把「的」漏掉。串轨迹时按文字相似度容错，单帧读错不会把一条字幕拆成两条。字幕文本拿来当转写时要人工过一遍。
- 测出来的甩镜只是候选，要看事件条确认；运动模糊太重时光流会追踪失败。
- 暗片子里的淡出到黑：前后镜头本来就暗（基线低于 0.12），不会被测成黑场，只会作为渐变剪辑点出现在 `dissolve` 候选里。看事件条时按实际情况标 `dip_to_black`，并填帧数。
- 标题颜色取笔画中心（文字像素里最亮或最暗的四分之一）。540p 下细字大部分是抗锯齿边缘，取全部像素的中位数会把白字测成灰色。
- 变速曲线（ramp_candidate）只是候选：摄影机运动本身的起停也会触发。
- 一致性判断（`stats.consistency`）比较的是各组中位数的离散程度，阈值 25%。只有一个参考时按章节分组，只出现在少数章节的标为单一来源。

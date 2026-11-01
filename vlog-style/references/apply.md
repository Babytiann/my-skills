# apply：按风格包剪用户的素材

输入是一个风格包（`vlog-style/styles/<风格>/style.json`）、用户的素材和一份要求（brief）。产出是一条在 Resolve 里能继续精修的时间线，加上手动收尾清单。下面的命令都在 `vlog-style/scripts/` 下运行；`P` 代表项目目录：

```sh
P=$(python3 -I -B paths.py resolve projects/<项目>)
```

## 1. 建项目、放素材

```sh
python3 -I -B paths.py init --project <项目>
```

会建出 `projects/<项目>/{footage,proxies,index,plan,qa}`。用户把原始素材放进 `footage/`（可以分子目录），音乐放进自己建的 `music/`。素材只读，脚本不会改它。

## 2. 检查素材、做代理

```sh
python3 -I -B prep.py footage --project <项目>
```

每个片段生成 `proxies/<片段>/`（`prep.json`、540p 原帧率代理、两条 WAV），汇总写进 `index/prep.json`。片段 id 是文件名去掉扩展名、特殊字符换成 `_`。检查输出里的三项：

- **帧率**：59.94 还是 60。plan_cut 会按素材的多数自动把 30 换成 29.97。
- **可变帧率**（`vfr: true`）：代理帧号和源素材帧号可能对不上，告诉用户，建议先转成恒定帧率。
- **传输特性**：用户的素材是标准的 bt709（`hdr: false`）。出现 HLG 或 PQ 时先问用户，look DRX 是按 Rec.709 做的。

## 3. 素材索引

逐个片段测量，跳过事件条（素材不需要）：

```sh
for d in "$P"/proxies/*/; do
  uv run --python 3.13 --script measure_video.py "$d" --skip strips
  uv run --python 3.13 --script measure_audio.py "$d" --beats-engine librosa   # 没有人声的片段加 --no-asr
done
```

然后 Claude 看每个片段的 `sheets/`，写 `index/tags.json`：

```json
{"clips": {
  "C0001": {"tags": ["tromso", "aurora"], "shot_type": "wide",
            "shots": {"1": {"shot_type": "close", "tags": ["tromso"], "reject": true}}}
}}
```

- 片段级的 `tags` / `shot_type` 是默认值，`shots` 里按镜头序号覆盖。
- `tags` 用 brief 里章节和段落的标签词。
- `shot_type` 用 aerial / wide / medium / close / detail / selfie（对着镜头说话或自拍）这几个词，和风格包 `selection.shot_types` 用同一套。
- 拍糊了、穿帮、不能用的镜头标 `reject: true`，plan_cut 不会选它。

最后汇总成 plan_cut 用的索引：

```sh
python3 -I -B aggregate.py footage --project <项目>
```

`index/footage.json` 里每个镜头有：start / end（源帧号，end 不含）、shot_type、tags、motion（static / pan / tilt / push_in / pull_out / complex / handheld；只有 static 的镜头会被加数字推拉）、sharp（项目内的清晰度排名，0–1）、exposure_ok、shake（抖动 RMS 占画面宽的 %）、best（最清楚的一帧）、speech（人声段，源帧号）。

## 4. 音乐拍点

```sh
uv run --python 3.13 --script measure_audio.py --music "$P/music/<曲子>" --out "$P/index/beats.json"
```

拍点时间从音乐文件开头算起。brief 里的 `music[].start_s` 表示从曲子第几秒开始用，plan_cut 会自己减掉这个偏移。beat_this 的权重没下好时用的是 librosa，`engine` 会写明，这时没有重拍，`use_downbeats` 不起作用。

## 5. 要求：plan/brief.json

```json
{
 "project": "norway",
 "duration_s": 180,
 "seed": 1,
 "chapters": [{"name": "特罗姆瑟", "tags": ["tromso"], "title": "特罗姆瑟 · 挪威", "card": null, "weight": 1}],
 "hook_tags": ["aurora"], "intro_tags": [], "interlude_tags": [], "ending_tags": ["dusk"],
 "music": [{"source": "projects/norway/music/track.wav", "start_s": 12.0}],
 "avoid": ["C0005"], "prefer": ["C0012"]
}
```

| 字段 | 含义 |
|---|---|
| `duration_s` | 成片时长 |
| `seed` | 随机种子，换一个就换一版剪法，同一个种子结果完全一样 |
| `fps` | 可选，覆盖风格包的交付帧率 |
| `chapters` | 按顺序的章节：`tags` 选镜头，`title` 地点标题，`card` 章节卡，`weight` 时长权重 |
| `*_tags` | 开头钩子、片头、过渡段、结尾各用哪些标签的镜头。片头和结尾不填时，默认用第一章和最后一章的标签 |
| `music` | 音乐，路径相对 root |
| `avoid` / `prefer` | 不用 / 优先用的片段 id |

风格包只管「怎么剪」，拍了什么、讲什么都写在 brief 里。

## 6. 生成剪辑计划

```sh
python3 -I -B plan_cut.py --style <风格> --project <项目> --explain
python3 -I -B duck.py --project <项目>          # 有人声又有音乐时
```

`plan/cut_plan.json` 里全是整数帧号：`src_in`/`src_out` 是源帧号，`rec_in`/`rec_out` 是时间线帧号（0 是时间线起点），end 都不含该帧。`checks.ok` 必须为 true。`warnings` 要转述给用户。

把 `--explain` 的表格和相关片段的总览图给用户看，**用户确认后才进 Resolve**。不满意就改 brief、tags 或换 seed 重新生成。

## 7. 用 MCP 搭时间线

前提：
- 用户同意打开或新建工程。
- Resolve 偏好设置 System > General 里「External scripting using」为 Local。
- 本会话里能看到 MCP 工具。注册 MCP 之后要重启 Claude Code 才能看到。

工具名以 MCP 工具列表为准。每一步都检查返回值，失败不会报错（见 resolve-routes.md 常见坑第 1 条）。

1. **工程。** 先保存用户当前的工程。新建 `vlog-<项目>-v<n>`；试剪用 `vlog-style-QA-<项目>`。
2. **设置。** 分辨率、帧率取 `cut_plan.timeline`，色彩管理设为 Rec.709 Gamma 2.4。必须在导入任何媒体之前设好：帧率在时间线有媒体后就锁定了，WAV 也会锁定导入时的帧率。
3. **导入。** 用 `paths.py resolve <相对路径>` 现拼绝对路径，导入 `video[].source`、压低版音乐床（`plan/ducked.json` 里的 `bed`）和原始音乐。绝对路径不写进任何文件。
4. **放片段。** 用 V1 的全部条目一次 append：每条 clipInfo 是 mediaPoolItem、`startFrame=src_in`、`endFrame=src_out`、`recordFrame=起始帧+rec_in`、`trackIndex`。`audio: mute` 的条目只放视频（mediaType 1）。先读回时间线起始帧，不要假设从 0 开始。
5. **读回核对。** 读回每个条目，整理成 compare.py 的格式（见下一节），跑 `compare.py readback`。不通过就删掉这条时间线，查原因后重建新版本。已经放上去的条目无法移动或修剪。
6. **转场。** 从最后一条往前，对有 `transition_out` 的条目按 `transition_out.resolve` 的 type 和 category 加转场，位置在条目尾部。
7. **变速。** 对 `speed` 不是 1 的条目调 `set_speed`。时间线是 50p 或 60p 时再加 `set_retime optical_flow`。append 后再改速度时时长会不会变，等探针第 10 项确认；不行就整条时间线改由 advanced server 生成 `.drt` 再导入。
8. **推拉。** 对有 `motion` 的条目，用 ImportFusionComp 导入 Transform 关键帧 comp，从 `from` 缩放到 `to`。comp 模板在探针时做好，放在 `<root>/styles/<风格>/fusion/`。
9. **标题。** `titles[]` 每条单独建一个时间线，在里面 `insert_fusion_title`（`template`）+ `set_title_text`，再回到主时间线，把那个时间线当片段放到 V3 的 `rec_in`，时长为 `rec_out − rec_in`。`style.position` 的原点在左上，Fusion 的 Center 原点在左下，写入时 y 要换成 1 − y。
10. **调色。** 有 `grade.drx` 时每个条目套用一次（套用会替换整个节点图）；只有 LUT 时用 `set_lut`。
11. **声音。** 压低版音乐床放 A2，原始音乐放 A3 并静音。片尾淡出等用 `set_fades`。
12. **字幕。** 按 style.text.subtitle 做，路线见 resolve-routes.md。
13. **标记。** `markers[]` 打到时间线上；`Timeline.AddMarker` 用相对时间线起点的帧号，直接用 `rec`。第一个标记是手动收尾清单，其中包括手动设置播放帧率。
14. **保存工程。**

## 8. 验收

**读回格式**（compare.py readback 的输入，帧号用绝对帧号，end 不含该帧）：

```json
{"start_frame": 108000,
 "video": {"1": [{"start": 108000, "end": 108090, "source_start": 0, "name": "C0001.MP4"}],
           "3": [{"start": 108600, "end": 108659, "name": "title-t001"}]}}
```

```sh
python3 -I -B compare.py readback "$P/qa/readback.json" --plan "$P/plan/cut_plan.json" --style <风格>
uv run --python 3.13 --script compare.py look "$P"/qa/stills/*.jpg --style <风格> --out "$P/qa/look.json"
```

- readback 要求每个计划条目都在、顺序对、帧号对。转场两侧的边允许偏差半个转场长度。镜头时长分布只报告，不判失败。
- 静帧：在每章挑 2–3 个镜头，把播放头放过去，用 `ExportCurrentFrameAsStill` 导出到 `qa/stills/`。look 不通过时按提示调 DRX，重新导出核对。
- 把静帧拼成总览，和参考的总览图（或风格包 `stills/`）并排看一遍，再交给用户。只读回不算通过，清单见 qa.md。

## 9. 导出（只在用户要求时）

先保存工程，再用 `render prepare_delivery_job` 排队：母版用 ProRes 422 HQ，发布用 H.265 Main10。传 `CustomName`，时间线名里有 `/` 或 `:` 会导致渲染失败。用户明确说要开始渲染才开始。

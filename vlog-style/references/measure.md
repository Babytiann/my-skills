# 测量与判读

参考片的分析只用本地测量，再由 agent 看抽帧拼图和事件条判读。参考片和用户素材都不上传到任何视频理解服务。时间一律以本地测量为准，agent 的标签只负责补上类型和描述。

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
| 6b. 配乐 | `uv run --python 3.13 --script measure_music.py "$R"`；判读前先跑过的，判读完再 `--rebuild` | `measure_music.json`、`measure_music.embed.json`。要 measure_audio.json；labels.json 的章节和歌词字幕特效拿来判换歌和核对人声。keep 留下的参考没有 music44k.wav，用 `--audio <重下的原片>` |
| 7. 汇总 | `python3 -I -B aggregate.py draft --name <风格名> "$R1" "$R2" … --out <风格包>/style.json` | 通过 schema 校验的 style.json 草稿。`R` 也可以是 keep 留下的 `styles/<博主\|misc>/refs/<id>`。`--out` 已存在时拒绝覆盖，加 `--force` 才覆盖；不加 `--out` 写到 `$R1/style.draft.json`。参考来自多个 mid 时打印警告 |
| 8. 留测量 | `python3 -I -B cleanup.py keep <id> --style <博主\|misc> [--yes]` | 不在 `R` 下，在 `<root>/styles/<博主\|misc>/refs/<id>/`：剥掉转写和字幕原文的 measure_video.json、measure_audio.json，labels.json，只留 6 个字段的 meta.json，measure_music.json 和 .embed.json 原样带上。之后才能 `cleanup.py trash <id>` |

几点说明：
- **OCR 跑在原片上，不跑代理。** 540p 下字幕只有 20 像素左右高，中文识别不可靠。OCR 默认把原片缩到 1080 高再识别。
- **先画面后声音。** 声音那一步要从画面结果里取烧录字幕，当作人声段，并优先用作转写。第 5 步再拿人声段回去，帮助给文字分类。
- **第一次运行会下载模型。**
  - torch 等依赖约 2 GB，已经下好。
  - beat_this 的权重 81 MB。服务器很慢，下载会被截断，所以不由脚本自动下载，要手动下到 `torch.hub.get_dir()` 下的 `checkpoints/beat_this-final0.ckpt`（默认 `~/.cache/torch/hub`，设了 `TORCH_HOME` 就跟着变）。没下好时脚本自动改用 librosa，`beats.engine` 会记为 `librosa`，这时没有重拍。
  - whisper large-v3-turbo 约 1.6 GB，第一次转写时下载。默认会跑；没有人声的片子，或者烧录字幕已经覆盖旁白时，加 `--no-asr`（先看字幕段有没有长空档）。
  - 选曲用的两个模型，第一次测音乐时从 Hugging Face 下载（2026-11-14 在 M6 / 24 GB、torch 2.15、transformers 5.23 上实测，30 分钟音频取 BV1sHqPBLExZ 前 1786 秒）：
    - CLAP 用 `laion/clap-htsat-unfused`（614 MB）：打曲风、情绪、乐器标签，算两段音乐像不像。30 分钟切成 178 个 10 秒窗，mps 上约 570 倍实时（几秒），加载 5 秒。拿测得的人声段核对：「有人说话」对「音乐」提示词区分人声窗口的 AUC 0.974。`laion/larger_clap_music` 不能用，见 learnings.md 2026-11-14。transformers 5 的 `get_*_features` 返回对象，取 `.pooler_output`（512 维，等于前向的 `*_embeds`），处理器参数是 `audio=`。
    - demucs htdemucs（84 MB，demucs 4.1.0 从 `adefossez/HTDemucs` 拉，旧的 dl.fbaipublicfiles.com 地址 403）：分出人声和伴奏。30 分钟 88 秒，约 20 倍实时，按 60 秒一段跑，峰值内存 1.9 GB。它分出的人声包括口播，不只是唱歌，见 learnings.md 2026-11-14。
- **在合成测试片上实测：** 16 秒的片子跑完全部测量约 10 秒。长片的耗时在第一次真实炼化时记到这里。博主模式的第一批（links 是 29–55 分钟）每步跑完都记一下耗时，再决定更长的片子要不要入池。
  - 实测（M 系列 Mac，1080p 下载，`--no-asr`，单位分钟；判读没有计时）：
    - 最孤独的房子（29:22）：prep 2.9、OCR 10.3、measure_video 7.3、measure_audio 0.7、`--redo text` 0.4，合计 21.6。另外修 bug 后重测 `--redo cuts` 7.0。
    - 库拉岗日（37:33）：prep 3.5、OCR 12.3、measure_video 8.5、measure_audio 0.8、`--redo text` 0.3，合计 25.4。
    - 第一座雪山（55:12）：prep 5.0、OCR 17.4、measure_video 13.7、measure_audio 1.5、`--redo text` 0.5，合计 38.1。另外有两次 prep 因 opencli 超时报失败（2.0、2.4），见 learnings.md。
    - 测量合计约是片长的 0.7 倍，OCR 占将近一半。
  - 2026-11-11 小鹿Lawrence 第一批（同一台机器，都是 `--no-asr`）：
    - 北海道4k旅拍（4:44）：prep 0.8、OCR 2.0、measure_video 1.2、measure_audio 0.2、`--redo text` 0.1，合计 4.3。
    - 日本4k旅拍（4:26）：prep 0.5、OCR 1.9、measure_video 1.2、measure_audio 0.1、`--redo text` 0.1，合计 3.8。第一次 OCR 因为 hev1 一帧没读到，按 learnings.md 2026-11-11 重跑了，这里记的是重跑的 OCR。
    - 北海道翻车实录（22:15）：prep 1.8、OCR 9.4、measure_video 5.0、measure_audio 0.5、`--redo text` 0.9，合计 17.6。measure_audio 和 `--redo text` 跑的时候，另一条片子也在测。
    - 5 分钟以内的短片约是片长的 0.9 倍：加载模型这类固定开销占得多。
- **先 keep 再 trash。** 留下的测量够重新 draft（结果和用 cache 跑的一样），但没有原片、代理和抽帧，不能重跑 measure 的各阶段。`measure_video.json` 的 `measured` 记的是每次跑的日期；旧文件没有这一项，keep 时用文件修改日期补上。

## measure_video.py

可以只重跑某几个阶段（`--redo cuts,scan`），上游重跑后，依赖它的阶段会自动重跑。阈值都写在脚本顶部的常量里，调整之后要重跑合成测试。

| 阶段 | 做法 | 关键输出 |
|---|---|---|
| cuts | TransNetV2（CPU）跑 48×27 小图，概率超过 0.5 的连续帧算一个转场。连续不超过 3 帧算硬切，用帧差把剪辑点精确到具体一帧；更长的算渐变，剪辑点放在中点。再用 PySceneDetect AdaptiveDetector 交叉核对（它的帧号先映射到恒定帧率时钟，错开的帧数记进 `psd_clock_shift`）：只有 PySceneDetect 发现的剪辑点，需要 TransNet 至少有 0.1 的弱信号，或帧差突跳，才收；不收的记进 `psd_rejected` | `cuts[]`：frame、t、kind（hard / gradual）、span、frames、src（transnet / psd / both）。`shots[]` |
| text | 把 OCR 结果串成轨迹，再分成字幕、标题、其他三类（stats.link_text / classify_text） | `tracks[]`、`subtitle_band`、`transcript`（字幕文本） |
| scan | 按 320 宽逐帧扫一遍，记录：亮度、清晰度（拉普拉斯方差）、过曝和死黑比例、LK 光流仿射（缩放、平移）。由此得到遮幅、每个镜头的运动，以及转场和特效的候选。闪白和黑场下的剪辑点会移到闪白或黑场的中点（`snap`），因为那里看不出画面在哪一帧切换；每次都从检测器给的原始剪辑点（`cuts[].raw`）算起，重跑 scan 不会叠加 | `letterbox`、`shots[]`（motion.type / zoom / zoom_eff / pan / shake、exposure_ok、best、ramp_candidate、freeze）、`events[]`、`keyframes[]` |
| look | 从每个镜头的关键帧测 Lab 数值，测之前遮掉遮幅、字幕带和 OCR 文字框。遮幅除了全片统一的，还逐个关键帧找只有这个镜头才有的纯黑边（风景段的 2.35:1 裁切、4:3 片段、黑底照片），上下或左右成对、对称、黑边以内有画面才算，所以暗夜镜头不会被当成黑边；字幕压在黑边上也认得出。整幅画面中间亮度不到 3 的（黑场、黑底字卡）照旧不计 | `summary`（每项的中位数和 MAD）、`hue_hist`、`per_shot[]`（带黑边的镜头多一项 `bars`：上下左右各占的比例）、`shots_with_bars` |
| strips | 在每个候选事件前后各取 0.5 秒，按原帧率拼成事件条。另外为每次标题出入场、每章抽几个硬切也各拼一条。标题颜色在 Lab 三通道上测（见下面「文字颜色」）。字幕线上（居中、在字幕带上、字高够）均匀取最多 12 条字幕，测颜色、描边和底板，不留文字。推拉镜头最多抽 24 个，各取 10%、50%、90% 三帧拼成 `strips/moves_*.jpg`，给 agent 判断是剪辑加的还是机位在动 | `events[].strip`、`titles[]`（含 colour：fill、edge）、`subtitle_samples[]`（track、t、colour：fill、edge、plate）、`moves[]` |
| sheets | 用关键帧拼成总览图，每张 6×8 格。10 分钟以内的片子每个镜头取 3 张，更长的取 1 张 | `sheets[]` |

运动类型的判定阈值：
- `push_in` / `pull_out`：镜头内总缩放 ≥1.04 或 ≤1/1.04，并且是一个方向的稳定变化：`zoom_eff` ≥0.95。`zoom_eff` 是每 0.5 秒一段的对数缩放，净值除以各段绝对值之和。人从画面走过、转身、视差，都会让缩放来回变，`zoom_eff` 就低。
- `pan_*` / `tilt_*`：每秒移动 ≥0.04 个画面宽。
- `complex`：总缩放过了 1.04，但不稳定、也不是摇或俯仰。多是人或车在画面里动，或者机位边走边转。
- `handheld`：抖动 RMS ≥0.12% 画面宽。

`push_in` / `pull_out` 只说明画面在稳定地放大或缩小，不区分原因。无人机前飞、车载、步行、拿着东西往镜头凑，都会测成推拉；剪辑里加的数字推拉也是。plan_cut 只会给静止镜头加数字推拉，所以风格包里的推拉比例只按 agent 判为 `digital` 的算（见判读清单第 2 步）。

`shake` 的单位是「抖动 RMS 占画面宽的百分比」，`selection.max_shake` 用的是同一个单位。

转场候选的判定：
- `flash_white`：亮度比剪辑点两侧的基线高 0.2 以上，且峰值高于 0.6。
- `dip_to_black`：最低亮度低于 0.06，且基线高于 0.12。
- 闪白和黑场的跨度都取到偏离量的 20% 处，而且两头都要在剪辑点前后 0.4 秒内回到基线。一直黑到窗口边上的，是硬切进了一个暗镜头（极夜、夜景），不算黑场；一直亮到边上的是切进了雪景，不算闪白。黑底字卡超过 0.4 秒的，也是两个硬切夹一个黑镜头，在标题里记成章节卡。
- `whip`：在剪辑点附近、转场范围外的帧里，清晰度掉到镜头中位数的 0.4 以下，同时平移超过 0.03 个画面宽，或者光流追踪失败 ≥3 帧。
- `zoom`：剪辑点附近有一帧的缩放超过 4%。

文字颜色（`text_colour`，标题和字幕样本都用它；learnings.md 2026-11-13）：
- 文字框和外圈一起在 Lab 三通道上分 5 类。框里占比最大、外圈也有的一类是底（画面或字幕底板）；在框里比外圈多 1.5 倍以上的是字的候选。只看亮度会出错：橙红字的亮度和雪地差不多。
- 候选里取离底最远、又最少贴着底的一类：阴影贴着底板，描边和抗锯齿包着填充色。颜色取这一类里离底最远的 10%，就是笔画中心。
- 描边（edge）：字旁边一圈比字暗、也比旁边的局部背景暗 12 以上，记 dark；亮的记 light。和局部背景比，所以橙字压过雪地和深色沙滩时，沙滩那段的暗边不算描边。
- 底板（plate，只测字幕）：字上方一点，同一行上七成以上的列一起变暗，就是底板的上沿。上方画面太暗（L < 15）时看不出暗底板，记 null，aggregate 汇总时不算它的票。

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

卡点可不可信由 `aggregate.py draft` 判，每条参考单独判，结果和原因在 `evidence.beat` 的 `reliable`、`why`。下面任一条成立就不可信（learnings.md 2026-11-13）：
- 非人声段里的剪辑点少于 20 个（`stats.MIN_BEAT_CUTS`）：基线 6% 时，13 个剪辑点随机平移一个都命中不到，命中率说明不了什么。
- `beats.confidence` 是 uncertain（非人声段太短）。
- beat_this 和 librosa 的速度对不上（`tempo_check.agrees` 为 false；半速和倍速算对得上），并且命中率不到基线的 1.5 倍。对不上但命中率高出基线很多的照样可信：剪辑点跟着 beat_this 的拍点走，说明它的网格是剪辑师用的那个。

过半参考可信，`beat.confidence` 才记 ok，命中率、基线、offset 只用可信的几条算；否则记 uncertain，plan_cut 就不卡点。

给用户的音乐生成拍点网格：

```sh
uv run --python 3.13 --script measure_audio.py --music <音乐文件> --out <项目>/index/beats.json
```

## measure_music.py

参考片怎么用配乐。只测量，不听（SKILL.md §2）。demucs 把声音分成人声和伴奏（60 秒一段），每 10 秒一个窗：

- `music`：伴奏轨的 CLAP 分数，最像的音乐提示词减最像的环境声提示词。口播已经分走，所以说话不会被当成音乐。大于 0.1 且伴奏中位电平高于 −50 dB 算有音乐。
- `sing`：人声轨的 CLAP 分数，唱歌提示词减说话提示词。demucs 的人声轨包括口播，歌词字幕又落在 measure_audio 的人声段里，两样都分不出唱和说，只能靠这个。大于 0 且人声轨高于 −45 dB，并且连着两个窗以上，才算在唱。
- `acc_db`、`voc_db`：两条轨每秒电平的窗内中位数；`speech`：窗内落在人声段的比例。

有音乐的窗连成段，中间只空一个窗、前后音乐又像（CLAP 余弦 ≥ 0.80）就接上。段内换歌：某个窗前后各 30 秒的音乐余弦 < 0.70，且 6 秒内有 measure_audio 的换曲候选（静音、拍点中断、变速）；< 0.55 时没有候选也切。不到 20 秒的碎段并进更像的一边；单独一段不到 20 秒的是 sting，单独计数。

换歌的阈值落在边上时，demucs 在 mps 上的细微差别就能让结果翻转：BV1Rj421S7c4 两次跑的窗向量余弦都在 0.978 以上，40 s 那条边界一次 0.698、一次 ≥ 0.70。翻的多半是口播下的铺底和后面的歌分不分开，所以短的铺底单独标 `bed`，不算歌，分不分开都不影响歌数和换歌规则。

| 字段 | 含义 |
|---|---|
| `coverage` | 有音乐的时长占全片 |
| `enter_s`、`exit_s` | 第一声和最后一声音乐 |
| `body_s` | 音乐真正铺开的时刻：第一次连着两个窗都在全片常态电平（没口播时的伴奏中位数）8 dB 以内。开头的短 sting、口播下面小声的铺底都不算 |
| `fade_in_s`、`fade_out_s` | 音乐开头、结尾到稳定电平 3 dB 以内用了几秒（平滑后） |
| `under_speech` | 口播的秒数里有音乐的占比（唱歌的窗不算口播） |
| `duck_db` | 没口播时的伴奏中位数减口播时的；两边都要有 20 秒以上 |
| `talk_s`、`ambient_only_s` | 口播秒数；没音乐也没口播、但有声音的秒数 |
| `usual_db` | 全片常态电平：没口播时的伴奏轨中位数 |
| `songs[]` | `t0`、`t1`、`level_db`（这一段的伴奏中位电平）、`bed`（不到 30 秒、比常态低 8 dB 以上：很短的小声铺底，不算一首歌；长的小声段是压在口播下的歌，照样算歌）、`bpm`（measure_audio 的拍点在这首歌里的速度）、`vocal_share`（有音乐的窗里在唱的占比；≥ 0.3 vocal，≤ 0.05 instrumental）、`verdict`、`tags`（伴奏向量的 CLAP 标签，每类前 3）、`at_chapter_cut`（开头离章节边界 6 秒内）、`start_sure`（开头那条边界余弦 < 0.65 且 3 秒内有候选） |
| `stings`、`bed_s` | 单独的短音乐；铺底一共几秒 |
| `songs_per_10min`、`change_at` | 每 10 分钟几首（不算铺底）；换歌多半在章节边界（≥ 60%）为 chapter，否则 within_chapter，只有一首为 none |
| `energy_curve` | 全片十等分，每段音乐电平的功率平均，最响一段为 0 dB（没音乐的秒按 −90 算） |
| `edges` | 每条换歌边界的时刻、余弦、离候选多远 |
| `doubts` | labels 里有歌词字幕特效、却没有一首测成有人唱 |
| `windows` | 每窗的上面几项，复查用 |

耗时（2026-11-14 补测 15 条，M6 / 24 GB，mps）：4–5 分钟的短片 21–23 秒，大半是加载模型；20–37 分钟的片子 83–142 秒；55 分钟的雪山 176 秒。约是片长的 7%，demucs 占大头；`--rebuild` 只加载 CLAP 文本端，几秒一条。

读到的声音比 measure_audio 记的片长短 5 秒以上（长片按 2%）时停下，不写结果：重下的原片音轨可能解不全，B 站源流有坏分片时重下也一样，修法见 learnings.md 2026-11-14。

`measure_music.embed.json` 存每窗和每首歌的 CLAP 向量，外加每秒的两条轨电平（`acc_s`、`voc_s`）。改了上面的阈值后 `--rebuild` 只重算分段，几秒钟，不跑 demucs。两个文件都没有片子里的文字，也不记歌名。

## 判读清单

判读不听声音，声音相关的结论只能来自测量和转写（SKILL.md 第 2 节）。看图时，顺序如下：

1. **总览图（sheets）+ 字幕。** 划分结构：开头钩子、片头、按地点分章、过渡段、结尾。看每章的开场镜头是什么样的。镜头标签里，`P+`/`P-` 是推和拉，`<`/`>` 是摇，`^`/`v` 是俯仰，`Z~` 是不稳定的缩放，`~` 是手持。
2. **推拉镜头（strips/moves_*.jpg）。** labels.json 的 `shots` 里预填了抽样的推拉镜头，逐个填 `move`：
   - `digital`：整幅等比放大或缩小，前景和背景之间没有视差，遮挡关系不变。剪辑里加的推拉、变焦都算。
   - `camera`：机位真的在动（无人机、车、步行、稳定器），或者是被摄物朝镜头移动。前景比背景放大得快，遮挡关系在变。
   - 高空航拍前飞几乎没有视差，和数字推镜很像，这种 `sure` 填 false。
   - 拼图上看不准时，取镜头 10%、90% 处的两帧，分别算画面上半和下半的缩放：差得多是视差，判 camera；两帧缩放都是 1.000 的是固定机位，测到的推拉来自画面里的人或水汽在动，也判 camera；上下一样大分不出来，按 camera 填 `sure: false`。
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
7. **调色。** 一句话概括基调。数值以 look 的测量为准，判读只写概括，不改数值。
8. **选镜头规律。** 景别配比（`shots[]` 里标 shot_type）、开场用什么镜头、哪类镜头从来不用。

### 判读约定

links 第一批三条定下的，以后的片子照这个标，统计才比得上。碰到新情况先按最接近的一条，再把决定记下来。

**写进 labels.json：** 同一个镜头号只留一条，`move` 和 `shot_type` 写在同一条里，后写的会整条覆盖前面的。不摘录台词，notes 只写结构和判断依据。

**章节：** role 只用 hook / intro / chapter / interlude / ending。按地点和时段分章，开始时间对齐测到的剪辑点。赞助口播标 interlude，单独一段。

**景别：** 只用 wide / medium / aerial / selfie / detail / close。

| 情形 | 标法 |
|---|---|
| 拍整间房、没有人或人很小 | wide |
| 以人为主；车内固定机位拍两人；别人对镜头讲、机位不在主持人手里 | medium |
| 上面这种，脸占大半个画面 | close |
| 主持人手持对着自己（含同框自拍）；脸或肩在画框边、手持拍另一个人 | selfie |
| 架子、墙上的照片、手里的东西 | detail |
| 赞助段、AI 生成插入、CG、地图、屏幕录像、照片插入、黑底卡、黑镜头、0.5 s 以下的碎镜头 | 不标 |

可能不是博主自己拍的画面（素材库、NASA 这类）照常判断，在 STYLE.md 里注明来源或「来源不明」，不算博主的拍法。

**慢动作：** 看图判断不出来就不写 `slow_mo` 这个键，在 STYLE.md 标「存疑」。要写就给每个标了景别的镜头都写明 true 或 false，只标 true 会把占比算成 100%。

**转场：** 眩光造成的误切标 `none`。同一次黑场被测成淡出、淡入两条的，后半段标 `none`，免得多算一次（first-snow-mountain 当时标成了 `dip_to_black`、`sure: false`，所以 10 条标签只有 8 处）。拍摄时做的转场（手挡镜头）标 `other`，写进 note，汇总后从 style.json 的 `transitions` 去掉，否则 plan_cut 可能抽中。片尾整片淡出到黑记 `dip_to_black`，带帧数（库拉岗日、第一座雪山都是这样记的），STYLE.md 第 6 节写明哪一次是片尾淡出。

**连续镜头里的假剪辑点（第二批补充）：** 快速手持甩动、车门打开转到雪地、运动相机放下这类，测到的剪辑点其实在一个镜头里，候选标 `none`。STYLE.md 第 4 节写明多算了几个剪辑点、哪几个镜头号其实是一个镜头；数量少时不改测量。低帧率的步进镜头（画面隔好几帧才更新一次）也会被 PySceneDetect 每隔几次更新算一个剪辑点，看帧差：一串间隔相同、中间全是 0 的小跳变就是步进，不是剪辑。假剪辑点把很多 0.5 s 左右的碎片带进来、拉低了 `min_shot_s` 时，按合并后的镜头重算 `rhythm` 手改，写进炼化记录（京都那条 P5 从 1.67 s 被拉到 1.03 s）。

**标题：** 每条都要填 role，不填会默认成 location。叠加的文字如果不是地点标题，role 记 `other`，note 记用途：

| note | 内容 |
|---|---|
| `name_tag` / `callout` | 人名条 / 强调字 |
| `sponsor_overlay` | 赞助段的产品字 |
| `time_stamp` / `day_stamp` | 时间戳 / 第几天 |
| `altitude_stamp` | 海拔和氧气角标 |
| `photo_counter` / `photo_signature` | 照片张数 / 照片旁的手写签名 |
| `series_title` / `end_card` | 手写系列标题 / 片尾标识（手写或印刷体） |
| `card_second_line` / `title_fragment` | 黑底卡被拆出的其余几行 / 同一幅字被拆出的其余部分 |
| `subtitle_misread` | 字幕被分成了标题（包括被 OCR 拆出来的一截字幕）；`cleanup.py keep` 按这个 note 清掉原文 |

计数类的叠字（拖车的第几轮）也记 `callout`。黑底卡每张一条 `chapter_card`。特效的 `type` 先用已有的名字：`photo_insert`、`film_photo_insert`、`photo_montage`、`map_insert`、`map_annotation`、`screen_capture`、`time_stamp`、`altitude_stamp`、`location_pin`、`callout`、`series_title_overlay`、`end_card`、`colour_bars`、`split_compare`、`split_screen`、`picture_in_picture`、`stock_footage`、`long_take`、`cg_insert`，都不合适才起新名字。

**写包时才发现的特效先补进 labels.json，再重新 draft，不直接加进 style.json。** 博主级包的 effects 只从各参考的 labels 汇总，直接加在子包里的到不了博主级包（learnings.md 2026-11-13）。改名也一样：同一种手法在 labels 里就改成包里用的名字。`aggregate.py draft` 会拿只用这一条参考的子包或 misc 包比一遍，子包有、labels 没有的逐条警告，改了名的在括号里标出 labels 里的名字。

同一张卡或同一幅标题被 OCR 拆成几条时，只留一条作 location 或 chapter_card，其余记 `other`。手写的标题放进 `effects`，这样 `text.title` 只反映地点标题的字体和位置。

## labels.json

由 `aggregate.py labels` 生成骨架，agent 只填空格。例子：

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
- 测到了但判读没标的转场，按测量值计入，同时列进 `evidence.unverified`。
- 镜头的 `move` 只能是 digital 或 camera。推拉比例 = 测到的推拉占比 × 判过的里面 digital 的比例；一个都没判时按全部算，同时记进 `evidence.unverified`。判断的计数在 `evidence.moves`。
- 转场的 `direction` 只写给 whip 和 wipe（`schema.DIRECTED`）；测量的 `whip_dir` 只在类型是 whip 时才用。甩镜候选判成叠化的，草稿里叠化不带方向。
- 多参考合并时不悄悄挑一条：参考之间写法不同的文字（grade.summary、grade.grain、audio.mood、selection.opening）留空，记进 unverified，原文只打在 stderr；look 只留各条一致的项（style-schema.md「专辑」）；一条片子里位置或字高差得多的标题卡逐张打出来，记进 `evidence.text_spread`；每条特效带 `ref`。
- 子包里有、labels 里没有的特效会警告（见判读约定「写包时才发现的特效」）。

## 已知限制

- 代理是 540p，加上 B 站二次压缩，颗粒和色带都不可信，只看大的色调。
- Vision OCR 会漏字，比如把「远处的山」读成「远处朐山」，把「的」漏掉。串轨迹时按文字相似度容错，单帧读错不会把一条字幕拆成两条。字幕文本拿来当转写时要人工过一遍。
- 测出来的甩镜只是候选，要看事件条确认；运动模糊太重时光流会追踪失败。
- 暗片子里的淡出到黑：前后镜头本来就暗（基线低于 0.12），不会被测成黑场，只会作为渐变剪辑点出现在 `dissolve` 候选里。看事件条时按实际情况标 `dip_to_black`，并填帧数。
- 文字颜色取笔画中心（字那一类像素里离底最远的 10%）。540p 下很细的字几乎全是抗锯齿边缘：links 的字幕笔画只有 1–2 像素，浅灰字会测得偏暗（L 约 62）。写包时字幕颜色对着截图核一下。字幕样本是 2026-11-13 才加的，之前的测量（包括 links 的 12 份保留副本）没有，草稿里的字幕颜色、描边、底板不写。
- 变速曲线（ramp_candidate）只是候选：摄影机运动本身的起停也会触发。
- 一致性判断（`stats.consistency`）比较的是各组中位数的离散程度，阈值 25%。只有一个参考时按章节分组，只出现在少数章节的标为单一来源。

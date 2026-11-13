---
name: vlog-style
description: "把剪得好的旅行 vlog（B 站链接或本地文件）炼化成风格包：用本地测量（ffmpeg、TransNetV2、光流、Apple Vision OCR、beat_this）加 agent 看抽帧拼图和事件条，得出结构、节奏、卡点、转场、运动、调色、文字、声音的完整选素材加剪辑流程；风格包可以按 B 站博主建专辑（一个博主一个文件夹，多条视频合并），只喜欢某一条视频的放进 misc，每个包都写明出处；再按风格包、用户的素材和要求生成逐帧精确的剪辑计划，通过 DaVinci Resolve MCP 搭出可继续精修的时间线，用户要求时才导出成片。用于炼化、学习或复刻某类 vlog 的剪辑风格，给某个博主建风格专辑，按某个风格剪素材，或根据反馈修改风格包。"
---

# 旅行 vlog 剪辑风格：炼化与复刻

用户找到几条剪得好的旅行 vlog，想学它们共同的剪法，再用自己的素材剪出一条类似的片子。这个 skill 分两半：

- **炼化**：测参考片，写出风格包 `styles/<名字>/`。风格包按 B 站博主分文件夹：博主专辑 `styles/<博主>/` 下一条视频或一个系列一个子包，炼够两条再合并成博主级包；只喜欢某一条视频的放进 `styles/misc/<名字>/`。
- **复刻**：按风格包挑用户的镜头、定剪辑点、加转场、变速、推拉、文字、调色、配乐，在 DaVinci Resolve 里搭出时间线。

交付的是一条可编辑的 Resolve 时间线，加上标在时间线上的手动收尾清单。成片只在用户要求时导出。默认用中文和用户交流。

细节在 references/ 里，做到哪一步读哪一篇：

| 文件 | 什么时候读 |
|---|---|
| [measure.md](references/measure.md) | 炼化：命令顺序、每个测量字段和阈值、判读清单、labels.json 格式 |
| [style-schema.md](references/style-schema.md) | 写或审风格包：style.json 每个字段、STYLE.md 模板 |
| [apply.md](references/apply.md) | 复刻：素材索引、brief、剪辑计划、MCP 搭建顺序、读回格式 |
| [resolve-routes.md](references/resolve-routes.md) | 任何 Resolve 操作：每个元素走哪条路线、探针清单、常见坑 |
| [qa.md](references/qa.md) | 交付前：炼化验收和复刻验收清单、已有的教训 |
| [learnings.md](references/learnings.md) | 开始之前翻一遍；被否掉的做法记在这里 |

## 1. 路径：只有一个根路径

`paths.toml` 是唯一维护路径的地方，里面只有 `root` 是绝对路径（现在是外接硬盘上的 `/Volumes/Meow Disk/vlog-style`）。换盘或迁移到 NAS 时整个目录搬过去，只改这一行；Resolve 工程里存的是绝对媒体路径，迁移后用 `MediaPool.RelinkClips` 指回新 root。root 在 `/Volumes/<卷名>/` 下时要先挂上那个盘：没挂载时，要读写 root 的命令会停下来说明原因；`paths.py show` 和 `check` 照常能跑，`show` 的 `volume_mounted` 报挂载状态。卷挂着、命令却一直不返回时，多半是 macOS 在等用户点「可移除的卷」授权，不要重跑，请用户看一眼弹窗（learnings.md 2026-11-10）。`VLOG_STYLE_ROOT` 环境变量可以临时覆盖 root（测试，或硬盘不在手边时）。

```
<root>/
  styles/<名字>/                 stills/（≤10 张低清关键帧）、luts/、drx/、fusion/
  styles/<博主|misc>/refs/<id>/  keep 留下的测量（转写和字幕原文已剥掉），重炼、补参考用
  projects/<项目>/               footage/（原始素材，只读）、proxies/、index/、plan/、qa/
  cache/refs/<id>/               参考片下载、代理、抽帧、拼图、事件条；炼化完先 keep，再移进废纸篓
```

- 风格包的文字部分（STYLE.md、style.json、sources.md）放在仓库里的 `vlog-style/styles/<名字>/`，派生素材放在 root 下。博主文件夹本身还有一份 sources.md，记出处和候选视频。
- 风格包的名字是 `名字` 或 `分组/名字`，只允许一层嵌套。分组是一个博主（比如 `links`）或 `misc`：`--style links` 是博主级合并包，`--style links/polar-night` 是其中的子包。分组目录下要放 `refs/` 和博主级包自己的素材目录，所以子包不能叫 `refs`、`stills`、`luts`、`drx`、`fusion`。ref id、项目名和分组名都不能带 `/`。
- `refs/` 只在 root 下，不进仓库：仓库是公开的。
- 所有 JSON 和 Markdown 里只写相对路径：项目文件相对 root，风格包素材相对风格目录。
- 只在调用工具的那一刻用 `scripts/paths.py resolve` 拼绝对路径，不写回任何文件。`paths.py check` 能查出违规的文件。例外：OCR 从片子里读到的文字如果只是以 `/` 开头（比如屏幕上的 `/bpm`），只列成提示，不计入绝对路径数；值里含真实文件系统路径的仍算违规。

## 2. 前提和环境

对跑这个 skill 的 agent 和机器的要求：

- **macOS。** OCR 用 Apple Vision（`vision_ocr.swift`），照片的拍摄时间和格式转换用 `sips`（`prep.py`、`photo_clips.py`），别的系统跑不了这几步。
- **agent 能在用户的机器上跑命令、读写 root 下的文件。**
- **agent 能看图。** 炼化的判读、复刻时的审片拼图（`story.py review`）、静帧和总览图核对都要看图。不能看图时只能测量、出草稿，判读交给用户。
- **判读不听声音。** 不管 agent 能不能听，声音的结论都只来自测量和转写，这样结果能复现。
- **搭时间线要 davinci-resolve MCP 和 Resolve Studio。** 外部脚本只有 Studio 能用；免费版只有 21.0.x 能走应用内桥接，21.1 起不行。没有 MCP 时停在 `cut_plan.json`，交给用户手动搭。

| 依赖 | 用途 | 说明 |
|---|---|---|
| ffmpeg / ffprobe | 探测、代理、音频、响度 | VideoToolbox 硬解硬编，不行时回退到 libx264 |
| uv | 运行带依赖的脚本 | 脚本头部用 PEP 723 写依赖，`uv run --python 3.13 --script`；不维护 venv |
| xcrun swift | `vision_ocr.swift` | Apple Vision 中文 OCR，直接解释执行，不留编译产物 |
| opencli + yt-dlp | 下载 B 站视频 | 借 Chrome 的登录状态；1080p 就够 |
| `trash` | 清理 | 一律移进废纸篓，不用 `rm` |
| davinci-resolve MCP | 实时搭时间线 | samuelgursky/davinci-resolve-mcp，注册为 `davinci-resolve` 和 `davinci-resolve-advanced`；注册后要新开一个会话（有的客户端要重启）才看得到工具 |

第一次运行时会下载模型：torch 等依赖约 2 GB；beat_this 权重 81 MB，要手动放到 `torch.hub.get_dir()` 下的 `checkpoints/beat_this-final0.ckpt`（默认 `~/.cache/torch/hub`，设了 `TORCH_HOME` 就跟着变），没有时自动改用 librosa；whisper large-v3-turbo 约 1.6 GB，第一次转写时下载。

纯标准库的脚本用 `python3 -I -B` 运行，其余用 uv。下载下来的文件只作为参数传给脚本，不在下载目录里运行任何东西。

## 3. 四种模式

| 模式 | 用户怎么说 | 产出 |
|---|---|---|
| **distill 炼化** | 「学一下这几个视频的剪法」「炼化这个 vlog」「给这个博主建个风格专辑」「我只喜欢这一条」 | `styles/<名字>/` 风格包（博主专辑的子包 `styles/<博主>/<名字>/`、博主级包 `styles/<博主>/`，或 `styles/misc/<名字>/`），用户审过 STYLE.md |
| **apply 复刻** | 「按这个风格剪我的素材」 | `projects/<项目>/plan/cut_plan.json`，用户确认后在 Resolve 里搭出时间线 |
| **refine 改风格** | 「转场太多了」「再加两个参考」 | 改过的风格包；能用新测量说明的改测量，属于口味的写进 STYLE.md 并标出来源 |
| **export 导出** | 「导出成片」 | Resolve 渲染队列里的任务，用户说开始才渲染 |

## 4. distill：炼化

命令和字段见 [measure.md](references/measure.md)。

1. **取片。** `prep.py ref <BV号|本地文件> --id <id>`。B 站视频下载到 `cache/refs/<id>/src/`；b23.tv 短链先解析成 BV 号。付费和充电专属的视频停下来告诉用户，不绕过。本地文件直接读，不复制也不动。
2. **OCR。** 对原片跑 `vision_ocr.swift --fps 4`。
3. **测画面。** `measure_video.py`：剪辑点、转场候选、运动、look card、文字轨迹、事件条、总览图。
4. **测声音。** `measure_audio.py`：响度、人声段、转写、拍点、onset、换曲候选、压低幅度估算。然后 `measure_video.py --redo text`，用人声段重新给文字分类。
5. **判读。** `aggregate.py labels` 生成骨架；agent 看 `sheets/` 和 `strips/`，按 measure.md 的清单把 labels.json 的空格填上。时间以测量为准，标签只补类型和描述；看不准的 `sure` 填 false。字体只判断类别。判读不听声音，声音的结论只来自测量和转写（见第 2 节）。
   - 测到的推拉多半是机位在动（无人机、车、步行），要看 `strips/moves_*.jpg` 逐个判 digital / camera，风格里的推拉比例只算 digital。
   - 转场候选误报很多（暗镜头、运动中切），每条都看事件条再定。
6. **汇总。** `aggregate.py draft --name <名字> <参考目录>… --out vlog-style/styles/<名字>/style.json`。对不上测量的标签会被拒收，列进 `evidence.rejected_labels`。多个参考时比较参考之间是否一致；只有一个参考时比较它的各章节之间，只出现在少数章节的标为「单一来源」。
   - 参考目录可以是 `cache/refs/<id>`，也可以是 keep 留下的 `styles/<博主|misc>/refs/<id>`。
   - `--out` 已经存在时拒绝覆盖，因为定稿的 style.json 里有手写的项。重炼时不加 `--out`，草稿写到第一个参考目录下的 `style.draft.json`，对照着把变化并回去；确实要整个覆盖才加 `--force`。
   - 合并的参考来自不同 UP 主（mid）时会打印警告。
7. **写风格包。** 按 [style-schema.md](references/style-schema.md) 的模板写 STYLE.md 和 sources.md，补完 style.json 里要看过画面才能判断的项（summary、mood、景别配比、慢动作），用 `schema.py style` 校验。从关键帧里挑不超过 10 张放进 `<root>/styles/<名字>/stills/`。
   - 写包时才发现的特效（或要改名的特效）先补进参考的 labels.json，再重新 draft，不直接加进 style.json：博主级包的 effects 只从 labels 汇总。draft 会拿只用这一条参考的包比一遍，包里有、labels 里没有的会警告。
   - 草稿 `evidence.unverified` 里列的项要逐条处理：参考之间写法不同的文字、不一致的颜色、混了几种卡片的标题、没测到的字幕描边和底板。处理结果写进炼化记录。
   - sources.md 开头是出处表：UP 主（名字、mid、空间链接）、视频（标题、链接、发布日期、时长）、测量日期，至少要有视频链接。style.json 的 `sources[]` 由 aggregate 从 meta.json 填好 url、title、author，和出处表对一遍。出处主要给人回溯，复刻时默认不去看原片（见第 5 节）。
8. **验收。** 过一遍 [qa.md](references/qa.md) 的炼化清单，再把 STYLE.md 交给用户审。
9. **试剪。** 用户给几段素材走一遍 apply（工程名 `vlog-style-QA-*`），对照关键帧和统计；问题改回风格包，记进 learnings.md。
10. **清理。** 用户确认风格包之后，先留测量，再清缓存：
    - `cleanup.py keep <id> --style <博主|misc> --yes`，把剥掉转写、字幕原文和简介的测量留到 `<root>/styles/<博主|misc>/refs/<id>/`，以后补参考、写博主级包时直接用。labels 里记 `subtitle_misread` 的标题（被 OCR 当成标题的字幕）也在这一步清掉原文，所以判读时误判的字幕一定要记这个 note。`--style` 只写顶层文件夹，不写子包。
    - `cleanup.py trash <id> --yes`，按 id 把 `cache/refs/<id>` 移进废纸篓。默认挡住三种：有进程正在用的，先停掉那个进程；没有 labels.json 也没有保留副本的，可能还在炼，有意放弃的下载加 `--unfinished`；有测量却没 keep 过的，确实不留的加 `--no-keep`。`trash --all` 跳过前两种并在 stderr 列出来，其余照清。每条的状态看 `cleanup.py list` 的 `state`。
    - 两步都先不加 `--yes` 跑一遍，给用户看要留下什么、要移走什么。

### 博主模式：给一个博主建专辑

1. **列投稿。** `opencli bilibili user-videos <mid> --limit 50 --page <N> --window background -f json`，翻到最后一页。
2. **核对 UP 主。** 投稿列表里有别人的合作投稿（比如影视飓风的视频会出现在 Links 的列表里），逐条 `opencli bilibili video <BV号> --window background -f json` 看 `mid`。不是这个博主投的不进专辑；用户喜欢的话放进 misc，出处写真实的 UP 主。
3. **分类。** 旅行片、器材和手机评测、音乐纯享、实验片分开，只挑和要学的风格同类的。都写进 `styles/<博主>/sources.md` 的候选表，每条写状态（入池 / 待炼 / 不入池）和理由。
4. **挑 3 条给用户确认，确认后才下载。** 挑法听用户的（links 第一批是旅行片里播放量最高的三条）；用户没说就先挑 20–30 分钟的。第一次炼长片时把耗时记进 measure.md。
5. **逐条炼化。** 每条走上面的第 1–10 步，ref id 直接用 BV 号，第 10 步用 `keep --style <博主>`。一条视频（或同一系列的几集）可以单独成子包 `styles/<博主>/<名字>/`。
6. **写博主级包。** 炼完至少 2 条之后：`aggregate.py draft --name <博主> "$(python3 -I -B paths.py resolve styles/<博主>/refs)"/* --out vlog-style/styles/<博主>/style.json`，basis 是 multi_reference。节奏按每条视频的中位数比较。调色跟着内容走（polar-night 各章中间调的 b 从 −21 到 +12），博主级包的 look 只写各条一致的部分，draft 按 style-schema.md「两种文件夹」的规则自动去掉不一致的项，调色核对用子包。

### 只要这一条：misc

用户只喜欢某个博主的某一条视频时不建专辑，走同样的第 1–10 步：风格包写到 `styles/misc/<名字>/`，第 10 步用 `keep --style misc`，sources.md 的 UP 主写视频真实的 UP 主。misc 顶层没有合并包。

## 5. apply：复刻

**什么时候回看参考片。** 默认只靠风格包本身：STYLE.md、style.json 和 `stills/`。风格包就是为了不用每次重看原片，sources.md 的出处主要给人回溯。下面三种情况可以回看：
- 这次要用的某一项包里没写清，或者标了「存疑」；
- 用户要求对照原片；
- refine 时要补测。

回看时先用本机已有的：先查 `<root>/styles/<博主|misc>/refs/` 里的测量和 `stills/`，不够再按出处链接重下这一条；能只看相关的时间段，就只看那一段。用完照旧 keep、trash；只看了一段、没有 keep 过的回看片段，trash 时加 `--unfinished`。回看得出的结论写回 STYLE.md 对应小节，标上日期和「回看补充」，同一个问题下次就不用再看。

详细步骤见 [apply.md](references/apply.md)。

1. `paths.py init --project <项目>`，用户把素材放进 `footage/`。
2. `prep.py footage` 检查帧率、可变帧率、传输特性，生成代理；照片读拍摄时间和时区，生成预览。
3. 逐片段 `measure_video.py --skip strips` 和 `measure_audio.py --sentences --language auto`；agent 看总览图写 `index/tags.json`（景别、标签、光线、哪些风景可移动）；`aggregate.py footage` 生成 `index/footage.json`。先让用户看一眼 `prep.py footage` 列出的各设备时间范围，时钟不对就在 brief 里写偏移。
4. 音乐：`measure_audio.py --music <曲子> --out index/beats.json`。
5. 和用户一起写 `plan/brief.json`：时长、章节和标签、地点标题、音乐、不用和优先用的片段，以及每章的调色参考 `look_ref`（光线和地方最像的子包）、夜景和暖光室内这类不修色偏的章。有对话时先 `story.py dump` 读转写，章节按真实发生的事分，agent 起草纸面剪辑 `plan/story.json`，**用户确认后**才往下走。
6. `plan_cut.py --style <名字> --project <项目> --explain`，有人声时再跑 `duck.py`。有 story 时 agent 先用 `story.py review` 的拼图自查时间、对话和画面、重复、跳接四项。把计划表和拼图给用户看，确认后用 `subs.py` 出字幕给用户校对，再跑 `grade.py solve` 按章算 CDL，摘要和警告转述给用户（撞边界、色偏修不到是素材的光线，如实说）。**计划和字幕都确认后才进 Resolve**。
7. 用户同意后，有照片时先 `photo_clips.py` 做照片短片，再用 MCP 按 apply.md 的顺序搭时间线，每一步检查返回值，每次 append 之后读回。调色按 `plan/grade.json` 给每个条目 `safe_set_cdl`。电平按 `mix.py dialogue` 写的 `plan/mix.json` 设；能渲染音频时，再用 `mix.py master` 把整片调到 −14 LUFS。
8. 验收：`compare.py normalize` 把 `timeline clip_where` 的回复整理成读回，再跑 `compare.py readback`；每章导出静帧用该章的 `look_ref` 跑 `compare.py look`、拼图和参考逐格对照、过 qa.md 的复刻清单。
9. 交付时间线，向用户说明手动收尾清单（标记在时间线开头，第一条是手动设置播放帧率）。用户要求导出时，渲染完按 apply.md §9 测成片，跑 `compare.py final`。`ok` 和 `complete` 都为 true 才算交付，不通过的项如实告诉用户。

## 6. refine：按反馈改风格

- 先判断反馈是「测错了」还是「口味不同」。测错了就查测量和标签，修正后重新 `aggregate.py draft`；口味不同就直接改 style.json 和 STYLE.md 对应小节，写明「用户调整」和日期。
- 补参考：新参考走 distill 的第 1–5 步，再把新旧参考一起 `aggregate.py draft`；basis 从单参考的章节比较变成多参考比较，一致性结论要重写。旧参考的缓存已经清掉了：先用 `<root>/styles/<博主|misc>/refs/` 里 keep 留下的测量，直接传给 `aggregate.py draft`；没留下的先去废纸篓找，找不到再按 sources.md 的链接重新下。重炼不要覆盖定稿的 style.json，见第 4 节第 6 步。
- 被否掉的做法记进 learnings.md。

## 7. export：导出

只在用户明确要求时做。先保存工程，再用 `render prepare_delivery_job` 排队：母版用 ProRes 422 HQ，发布用 H.265 Main10；传 `CustomName`。用户说开始才开始渲染。

## 8. 规矩

- **素材的事实优先于风格。** 冲突时按这个顺序让路：素材的事实（什么时候、在哪、谁说了什么）> 用户的要求（brief 和确认过的纸面剪辑）> 风格的数字。按时间铺开的内容（人物、事件、对话、行程）不能挪：三天后发生的事，不能因为标签或画面像就放到第一天。纯风景空镜没有时间进展，必要时可以借，但要同地点、光线不矛盾，并且标出来。口播说到什么，后面的画面就要是什么。风格的数字是范围和偏好，不是配额，达不到就如实报告原因，不为了凑数改内容。
- **不上传。** 参考片和用户素材都不发给任何视频理解服务，分析只在本机做。
- **refs 不进仓库。** 仓库是公开的。keep 留下的测量虽然剥掉了转写和字幕原文，也只放在 root 下的 `styles/<博主|misc>/refs/`。
- **每个包都写出处。** sources.md 的出处表和 style.json 的 `sources[]`（url、title 必填）要对得上。博主专辑只收本人投稿的视频，别人的放 misc。
- **版权。** 参考片只用于本地研究剪法。风格包只存测量出的参数和中性描述，不带参考片的画面、音乐、字体进用户的成片；参考片用完移进废纸篓（测量先 keep）。不绕过付费或充电内容，不给 opencli 传 `--force`。
- **Resolve。** 建工程、切工程由脚本自动完成，用户不用操作界面（做法见 resolve-routes.md「自动建工程」）。切走前先保存当前工程。不在用户正在剪的工程上直接试验，试验用 `vlog-style-QA-*` 一次性工程。不在仓库里跑 MCP 的 `install.py`。碰到只能人点的对话框就停下来告诉用户。
- **装东西、改配置先问。** 包括下载模型以外的任何安装、改 Resolve 偏好设置、改 MCP 注册。
- **只读回不算通过。** 调色、标题、Fusion 有没有生效要看导出的静帧。
- **删除一律用 `trash`。** 用户的本地参考文件和原始素材永远不动。

## 9. 测试

改了脚本之后在 `scripts/` 下跑：

```sh
for t in test_paths test_stats test_plan_cut test_tools; do python3 -I -B $t.py; done
uv run --python 3.13 --script test_synthetic.py     # 约 15 秒，需要 ffmpeg、uv、xcrun
```

`test_synthetic.py` 渲染一段答案已知的 16 秒测试片：硬切、12 帧叠化、6 帧闪白、摇镜、1.0→1.3 推镜、中文标题、底部字幕、120 BPM 节拍。依次跑 prep、OCR、measure_video、measure_audio、aggregate、plan_cut、compare、cleanup keep（再用留下的测量重炼、两份参考合并），检查剪辑点 ±1 帧、推镜比例 ±0.03、文字时间 ±0.25 秒、BPM ±1，以及所有输出里没有绝对路径。另有 grade.py 的按章调色：从已知 CDL 的结果解回来、在范围内不动、撞边界要警告、暗素材自动按夜景只压暗、照片不进拟合、`preview` 自比为 0。`KEEP=1` 保留临时目录，方便查看拼图。

## 10. 当前状态

- 测量、汇总、剪辑计划、压低、读回和调色对照已经写完，合成测试全部通过。
- Resolve 探针 2026-11-02 在本机（Studio 21.1.1.10 + MCP 4.15.3）跑完，12 项都有结论，写进了 resolve-routes.md 的「本机」一列和常见坑。换了路线的：标题写字（`fusion_comp bulk_set_inputs`）、调色（中性 DRX + merge + `graph set_lut`）、片段音量（`AudioVolume`）、推拉（最小 `.comp` 模板）、字幕 .srt（空时间线上旧式 append）。变速曲线的离线 `.drt` 否掉，改手动。还没探针：划像、遮幅、Glow。否掉的做法见 learnings.md 2026-11-02。Resolve 或 MCP 升级后重跑探针。
- 第一个参考是 BV1sHqPBLExZ（Linksphotograph《当太阳不再升起》第 1 集，约 30 分钟，内嵌字幕），已炼成 `styles/polar-night/`（2026-11-05 搬到 `styles/links/polar-night/`），用户已审过 STYLE.md。只有一个参考，按章节比较一致性；参考缓存已清，原始测量在有 keep 之前就随缓存进了废纸篓。
- 2026-11-05 改成按博主建专辑。`styles/links/` 是 Links（mid 3816626）的专辑，候选视频和理由在 `links/sources.md`。`misc/` 还是空的。
- 2026-11-06 第一批三条炼完：`links/lonely-house`、`links/kula-kangri`、`links/first-snow-mountain`（用户定的挑法：旅行片里播放量最高的三条），测量都 keep 到了 `<root>/styles/links/refs/`。博主级包 `links` 由这三份测量合并，basis = multi_reference，是多参考合并第一次用真实数据，手改项记在 `links/STYLE.md` 的炼化记录。polar-night 没有参与合并。三条的缓存 2026-11-06 移进了废纸篓，清不清空由用户定。试剪还没做：没有素材。
- 2026-11-07 复刻补上了对白片要的环节：纸面剪辑（`story.json`）驱动的对白剪辑和按拍摄顺序铺开、按句转写和外语原文、`subs.py` 出字幕、`story.py review` 审片拼图、照片入片（`photo_clips.py`）、按章调色（`grade.py`）、对白和整片电平（`mix.py`）、读回整理和成片核对（`compare.py normalize`、`final`）。没有 story 时输出和改动前逐字节相同（`test_plan_cut.py` 的 baseline 测试）。Resolve 探针 #13–#17 跑完，#17 用合成素材从计划走到 H.265 成片，`compare.py final` 全过。用真实素材的完整试剪还没做；Links 包里 `aroll` 除 `speech_share` 外的值和 `photo` 块标着「存疑」，等试剪再定。
- 2026-11-10 到 11-11 第二批九条炼完（用户按地点挑：日本四条、极地三条、极夜系列两集）：`links/hokkaido-dream`、`links/oki-islands`、`links/kyoto`、`links/aomori`、`links/uummannaq`、`links/longyearbyen`、`links/lofoten`，`links/polar-night` 由两集重建，替换 11-01 只用第 1 集的版本。测量都 keep 到了 `<root>/styles/links/refs/`，博主级包 `links` 由 12 份测量重新合并。途中改了四处：look 逐镜头遮黑边、2:1 参考的文字位置换算、字幕带取文字最密的一行、keep 清掉 `subtitle_misread` 的原文（见 learnings.md 2026-11-10、11-11）。第二批的缓存还在 `<root>/cache/refs/`，用户审过 STYLE.md 后决定先不清；以后清不清还是由用户定。
- 2026-11-13 修了炼化小鹿Lawrence 时发现的 5 个脚本问题：标题颜色测成背景色、叠化带上甩镜方向、剪辑点太少也判卡点可信、博主级合并只取一条参考的写法、子包手加的特效进不了博主级包（见 learnings.md 2026-11-13）。字幕的颜色、描边、底板改成测量，只有 2026-11-13 以后跑过 strips 的参考才有。2026-11-14 按新的测量改了定稿包：links 的 13 个包字幕改成不描边、垫底条；lawrence 三个包写明双语台词行才垫底板（japan-winter 的 plate 改成 false）；misc/lawrence-hokkaido 的卡点可信度改成 uncertain。各包的炼化记录里有一条。

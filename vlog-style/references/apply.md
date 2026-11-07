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

每个片段生成 `proxies/<片段>/`（`prep.json`、540p 原帧率代理、两条 WAV），汇总写进 `index/prep.json`。隐藏文件和隐藏目录会跳过（exFAT 卡上的 `._C0001.MP4`）。

片段 id 默认是文件名去掉扩展名、特殊字符换成 `_`。下面这些情况例外：
- 两张卡或两台机器有同名文件时，id 带上最近一层子目录（`cardB_C0001`）；直接放在 `footage/` 下的那个保持原名。
- 同一目录下同名不同扩展名时带上扩展名（`IMG_0001_heic`、`IMG_0001_mov`）。
- 已经做过代理的文件，id 永远不变，后面再加一张卡也一样，因为 tags、story、brief 都按 id 引用片段。

检查输出里的三项：

- **帧率**：59.94 还是 60。plan_cut 会按素材的多数自动把 30 换成 29.97。
- **可变帧率**（`vfr: true`）：代理帧号和源素材帧号可能对不上，告诉用户，建议先转成恒定帧率。
- **传输特性**：用户的素材是标准的 bt709（`hdr: false`）。出现 HLG 或 PQ 时先问用户，look DRX 是按 Rec.709 做的。
- **照片**：jpg、jpeg、heic、heif、png 和视频一起扫，`proxies/<id>/` 里只有 `prep.json` 和 640 px 的 `preview.jpg`。拍摄时间读 EXIF 的 DateTimeOriginal，时区读 OffsetTimeOriginal。没有时区的按本机时区读，设备名后面加「(no time zone)」，在外地拍的就给这个设备单独写 `clock_offsets_s`。导出过或经聊天软件转发的照片常常没有拍摄时间，输出会提醒：换原片，或者在 tags.json 里写 `shot_at`。RAW（dng、cr2、arw 等）还不支持，列在 `raw_skipped` 里，请用户导出成 jpg 或 heic。同一目录的 Live Photo（`IMG_0001.HEIC` 和 `IMG_0001.MOV`）分别是 `IMG_0001_heic` 和 `IMG_0001_mov`。
- **拍摄时间**：每个片段的 `prep.json` 记下 `shot_at`、时间来源和设备，输出末尾按设备列出时间范围。时间读自素材里的 `creation_time`，不用文件修改时间（复制素材会改掉它）；都没有时按文件名排，来源记成 `filename`。几台设备的时间范围对不上时（比如手机比相机快一小时，或者无人机写的是 UTC），在 brief 里写 `clock_offsets_s`（见第 5 节）；个别片段不对，在 tags.json 里直接写 `shot_at`。按时间剪全靠这些时间，先让用户看一眼设备时间范围。

## 3. 素材索引

逐个片段测量，跳过事件条（素材不需要）：

```sh
for d in "$P"/proxies/*/; do
  uv run --python 3.13 --script measure_video.py "$d" --skip strips
  uv run --python 3.13 --script measure_audio.py "$d" --beats-engine librosa --sentences --language auto
done
```

转写的参数：
- `--sentences`：把声音在静处切成不到 30 秒的段逐段转写，带词时间戳，再按句末标点、长停顿和换语言切成句子。plan_cut 按句子对齐 A-roll，所以 apply 默认加。不加时 prompt 只管得到前 30 秒，后面的转写基本没有标点，一句话会被切成几段（测试 A：1121 段里只有 24 段带句末标点）。
- `--language auto`：每段自己判语言。外语对话转成原文，在 footage.json 里标上语言，字幕由 agent 翻译（第 6 节）。全是普通话时用 `--language zh`，更稳。一段里混着两种语言时整段只按一种转，夹在中文里的外语可能转错，读转写时留意。
- `--prompt` 会替换默认提示，而默认那句负责简体和标点，所以要写成「默认那句 + 地名人名」：`--prompt "以下是普通话的旅行视频旁白，使用简体中文和标点。特罗姆瑟、罗弗敦。"`。
- 纯风景、航拍这类没有人声的片段可以加 `--no-asr` 省时间。有人说话的片段不要加：没有转写就没有句子，plan_cut 不能拿它做 A-roll。

照片会被两个测量脚本直接跳过。然后 agent 看每个片段的 `sheets/`（照片看 `preview.jpg`），写 `index/tags.json`，照片和视频用同一套词：

```json
{"clips": {
  "C0001": {"tags": ["tromso", "aurora"], "shot_type": "wide", "light": "night", "movable": true,
            "shots": {"1": {"shot_type": "close", "tags": ["tromso"], "reject": true}}},
  "C0007": {"shot_at": "2026-02-11T21:40:00+01:00"}
}}
```

- 片段级的 `tags` / `shot_type` 是默认值，`shots` 里按镜头序号覆盖。
- `tags` 用 brief 里章节和段落的标签词。
- `shot_type` 用 aerial / wide / medium / close / detail / selfie（对着镜头说话或自拍）这几个词，和风格包 `selection.shot_types` 用同一套。
- 拍糊了、穿帮、不能用的镜头标 `reject: true`，plan_cut 不会选它。
- `light` 取 day / golden / blue / night。借用风景时要看它，没标的不能借。
- `movable: true` 只给纯风景和空镜：没有人和事件，也看不出时间在往前走。这种镜头必要时可以离开自己的时间，借到同地点的别的章节；不标的都算有时间进展，不能挪。登顶才看到的景、当天才有的天气（比如第三天才下的雪）不算可移动，提前放就是剧透或穿帮。不要为了让 plan_cut 通过而乱标。
- `shot_at` 手动改这个片段的拍摄时间（ISO 8601，带时区）。以它为准，不再加 `clock_offsets_s`。

最后汇总成 plan_cut 用的索引：

```sh
python3 -I -B aggregate.py footage --project <项目>
```

`index/footage.json` 里每个片段有 shot_at（改过时钟之后）、order（拍摄顺序）、day（按长间隔分的第几天）和 sentences（句子，`[源入帧, 源出帧, 文字]`，外语句带第 4 项语言）。每个镜头有：start / end（源帧号，end 不含）、shot_type、tags、motion（static / pan / tilt / push_in / pull_out / complex / handheld；只有 static 的镜头会被加数字推拉）、sharp（项目内的清晰度排名，0–1）、exposure_ok、shake（抖动 RMS 占画面宽的 %）、best（最清楚的一帧）、speech（人声段，源帧号）、sentences（裁到镜头范围内），以及标了的 movable、light、reject。

## 4. 音乐拍点

```sh
uv run --python 3.13 --script measure_audio.py --music "$P/music/<曲子>" --out "$P/index/beats.json"
```

拍点时间从音乐文件开头算起。brief 里的 `music[].start_s` 表示从曲子第几秒开始用，plan_cut 会自己减掉这个偏移。beat_this 的权重没下好时用的是 librosa，`engine` 会写明，这时没有重拍，`use_downbeats` 不起作用。

## 5. 要求和纸面剪辑：plan/brief.json、plan/story.json

```json
{
 "project": "norway",
 "duration_s": 180,
 "seed": 1,
 "chapters": [{"name": "特罗姆瑟", "tags": ["tromso"], "title": "特罗姆瑟 · 挪威", "card": null, "weight": 1,
               "window": ["2026-02-11T08:00:00+01:00", null], "look_ref": "links/polar-night", "night": false}],
 "hook_tags": ["aurora"], "intro_tags": [], "interlude_tags": [], "ending_tags": ["dusk"],
 "music": [{"source": "projects/norway/music/track.wav", "start_s": 12.0}],
 "avoid": ["C0005"], "prefer": ["C0012"],
 "clock_offsets_s": {"DJI Mini 4 Pro": -3600},
 "photos": {"chapters": {"特罗姆瑟": [{"clip": "IMG_0012", "after": 2}, {"clip": "IMG_0013", "at": "end"}]},
            "ending": ["IMG_0100", "IMG_0101"]}
}
```

| 字段 | 含义 |
|---|---|
| `duration_s` | 成片时长。有 story 时可以不写，按 A-roll 总长 ÷ 风格的 `aroll.speech_share` 推 |
| `seed` | 随机种子，换一个就换一版剪法，同一个种子结果完全一样 |
| `fps` | 可选，覆盖风格包的交付帧率 |
| `chapters` | 按顺序的章节：`tags` 选镜头，`title` 地点标题，`card` 章节卡，`weight` 时长权重（有 story 时不写，按该章 A-roll 的长短定），`window` 可选的时间窗 `[开始, 结束]`（ISO 8601，一头可以是 null）。调色用三项（grade.py）：`look_ref` 这一章往哪个子包的 look 调，选和这章光线、地方最像的（如 `links/kula-kangri`；不写就用剪辑计划的风格包并警告，博主级的包色偏键少）；`night` 写 true 是夜景，只压暗不提亮、不修色偏（不写时源 L50 < 25 自动按夜景，会警告；写 false 就不自动判）；`cast` 写 false 不修色偏，用于暖光室内这类光线本身就是事实的章，默认 true |
| `*_tags` | 开头钩子、片头、过渡段、结尾各用哪些标签的镜头。片头和结尾不填时，默认用第一章和最后一章的标签 |
| `music` | 音乐，路径相对 root |
| `avoid` / `prefer` | 不用 / 优先用的片段 id |
| `clock_offsets_s` | 按设备名的时钟偏移（秒），加到这台设备所有片段的拍摄时间上。设备名取 `prep.py footage` 输出里的名字 |
| `photos` | 照片放哪里。`chapters` 按章列：`after: n` 紧跟在本章 story 第 n 条 A-roll 之后，中间不插 B-roll（`after: 0` 放在第一条之前；要有 story），`at: start` / `at: end` 放在本章开头或结尾。同一处的几张连着放。章内照片和视频一样按时间走：不在本章时间窗里、或者和前后条目的时间顺序不对就报错，要放就写 `out_of_order`。`ending` 是片尾回顾连放，不管时间顺序，结尾段不够长时从章节里匀时间。每张多长、多大取风格的 `photo`。同一张只能用一次 |

风格包只管「怎么剪」，拍了什么、讲什么都写在 brief 和 story 里。

### 纸面剪辑：plan/story.json

对话多的素材先写纸面剪辑，用户确认后再生成剪辑计划。没有这个文件时，plan_cut 照旧只按标签和打分选镜头，输出和以前一样。

1. 读转写：

   ```sh
   python3 -I -B story.py dump --project <项目> > "$P/plan/transcript.md"
   ```

   按拍摄时间分天、按长间隔分段，列出每个片段的转写。每行带真实时间、设备、景别和标签，外语句标出语言（如 `[en]`）。章节跟着真实发生的事走：一天或一段连续的行程一章，不为了凑风格的章数去拆或并。brief 的 `chapters` 也照这个写。
2. agent 起草 story.json，**给用户确认（确认点 1）**：

   ```json
   {"hook": [{"clip": "C0042", "in_s": 12.4, "out_s": 16.0}],
    "chapters": {
     "特罗姆瑟": [
      {"clip": "C0003", "in_s": 3.1, "out_s": 31.0, "note": "到达",
       "cover": [{"clip": "C0005", "in_s": 2.0, "out_s": 5.5, "at_s": 8.0}]},
      {"broll": true, "clip": "C0009", "in_s": 0.0, "out_s": 4.0},
      {"clip": "C0011", "in_s": 40.2, "out_s": 52.0, "note": "朋友们接下来欣赏极光"},
      {"showcase": "极光", "tags": ["aurora"], "dur_s": 20}
     ]}}
   ```

| 条目 | 含义 |
|---|---|
| `{clip, in_s, out_s, note}` | 一条 A-roll，时间是源时间（秒）。plan_cut 把它对齐到整句再留白：边界落在句子里向外扩，落在静音里向内收，范围内一句都没有就报错。可以跨同一片段里的多个镜头，不能跨片段 |
| `cover` | 盖在这条 A-roll 上的指定画面（放 V2），`at_s` 是从这条开始后第几秒盖上。说到「那家店」时盖那家店。没写的由 plan_cut 按风格自动盖 |
| `{broll: true, clip, in_s, out_s}` | 指定的 B-roll，原样放在 V1 |
| `{showcase, tags, dur_s}` | 风景段落。口播引出一段风景时（「朋友们接下来欣赏 XXX」）放在那句后面。画面必须匹配 `tags`；可移动的镜头可以跨时间取，其余的只能用本章时间窗内的；这段没有人声，按风格卡拍点；素材不够 `dur_s` 就报错，不拿别的风景凑 |
| `out_of_order: "理由"` | 可以写在任何条目上，允许它不按时间走，理由显示在计划表里 |
| `hook` | 冷开场的话，取自后面真实发生的片段，表里标「预告」，源帧可以和后面重复 |

章节名要和 brief 的 `chapters[].name` 一致。片段不存在、时间超出片段、章节名对不上，都会报错并指出是哪一条。

**按时间走。** 有 story 时默认按拍摄时间剪；只有风格写了 `structure.order: free` 才不按，这只用于用户明确要的混剪。
- 每章有一个时间窗：brief 写了 `window` 就用它；否则由该章的 A-roll 推出，边界取相邻两章的中点；再没有就取打了该章标签的镜头的时间范围。窗必须升序、不重叠。
- B-roll 先选本章窗内的。窗内不够时才借窗外的可移动镜头，条件是标签里有本章的地点，光线和相邻 A-roll 相容（夜里说话不接白天的风景）。还不够就报错，写明哪一章哪个空档差多少秒，以及可选做法：改 `duration_s` 或 `weight`、补素材、标 movable、写 out_of_order。
- 章内不倒退，往回超过 `order_tolerance_s`（默认 300 s）的不能选。靠文件名兜底、没有真实时间的片段，容差是 0。
- 列明的例外：hook（预告）、片尾照片连放、赞助段、写了 out_of_order 的条目。借用的镜头在计划表里标「借用 D3」，每章借了多少秒有汇总。

## 6. 生成剪辑计划

```sh
python3 -I -B plan_cut.py --style <风格> --project <项目> --explain
python3 -I -B duck.py --project <项目>          # 有人声又有音乐时
```

`plan/cut_plan.json` 里全是整数帧号：`src_in`/`src_out` 是源帧号，`rec_in`/`rec_out` 是时间线帧号（0 是时间线起点），end 都不含该帧。`checks.ok` 必须为 true。`warnings` 要转述给用户。

把 `--explain` 的表格和相关片段的总览图给用户看，**用户确认后才进 Resolve**。不满意就改 brief、tags 或换 seed 重新生成。

**按章算调色。** 计划确认后：

```sh
uv run --python 3.13 --script grade.py solve --project <项目>
```

每章从代理里取静帧（每个 V1/V2 条目源区间中点一帧，每章最多 12 张），解一个 ASC CDL（slope、offset、power 各三通道，加 sat），让这章的 look 落进 `look_ref` 子包的范围，写进 `plan/grade.json`。默认做法和例外：

- 已经在范围内的键不动（只罚超出半个容差的部分），所以每章保留自己的光线，不会被统一拉到同一个亮度。
- 夹值：slope 0.7–1.3、offset ±0.05、power 0.8–1.2、sat 0.7–1.3，色偏项 R/B slope ±0.06、offset ±0.02（起步值）。撞到边界会警告，这时剩下的差距是素材本身的光线，如实告诉用户，不为了过 look 检查硬拉。
- 夜景章只压暗：power 固定 1、slope ≤ 1、offset ≤ 0，只拟合 L01 和 chroma。
- 色偏只修目标里有的键，`cast: false` 的章不修。修不到的写在警告里，此时 §8 的 look 不通过可以接受，要写明原因。
- 照片跟所属章用同一个 CDL，但不参与拟合（手机已经处理过）。hook、片头、过渡段、结尾的条目跟拍摄时间落在其时间窗里的章走，不在任何时间窗里就跟最近的章；没有拍摄时间的跟时间线上最近的章。
- 一章里静帧的 L50 相差超过 20 会警告，建议拆章：一个 CDL 照顾不了两种光线。
- 代理是什么样就按什么样量。log 素材会大片撞边界，要先做转换（不在本步范围）。

stderr 的摘要表和 `warnings` 转述给用户。想用 Resolve 里导出的未调色静帧代替代理（比如代理色彩不准时），按章名分子目录放好，加 `--stills <目录>`。`grade.py preview --grade plan/grade.json --chapter <章> <静帧>...` 把某章的 CDL 套到静帧上出 PNG，可以先给用户看效果。

**有 story 时**，计划里多了 `dialogue[]`（A1 对白，每条带它的句子）、`ambient[]`（只在开了 `audio.broll_ambient_db` 时有）和 `story_stats`。`video[]` 每条带 `role`（aroll / broll / cutaway）、拍摄时间和例外标记，cutaway 在 V2。`--explain` 多了角色、拍摄时间和说明几列，A-roll 行显示首句，V2 和对白单独列表，还有 A-roll 占比、压平后的镜头中位数和风格的对照。差得多要说明原因，不为了凑数改内容。

给用户看之前，agent 先自己审片：

```sh
uv run --python 3.13 --script story.py review --project <项目>
```

每章一张拼图写到 `qa/review/`，按时间线顺序排。每格标拍摄时间、片段 id、例外（黄字）和这一刻听到的话（绿字），V2 的格子带蓝框。agent 逐张看，查四件事：
1. 时间地点对不对：第一天的章里没有第三天的人和事，借来的只有风景。
2. 说的话和画面对得上：说到那家店，画面是那家店；「接下来欣赏」后面是说到的东西。
3. 没有重复的画面。
4. 相邻两个镜头不跳：同一个人同一景别硬接、亮度突变。

有问题就改 story 或 tags 重新生成，然后把计划表和拼图给用户看（**确认点 2**）。

**照片短片**（brief 写了 `photos` 时）：

```sh
python3 -I -B photo_clips.py --project <项目>
```

Resolve 不按帧放原生静图（常见坑第 27 条），所以每张照片做成时间线帧率和尺寸的短片，写到 `proxies/<id>/photo.mp4`，也就是计划里照片条目的 `source`。整张照片等比放进画面，留黑边，不裁切；按 EXIF 方向转正；标 bt709。和原片的平均色差约 2/255，是转到和素材一样的 16–235 范围造成的，看不出来。已经做过、计划又没变的照片不重做；改了计划重跑就行。原片只读。

**字幕**（有人声就做）：

```sh
python3 -I -B subs.py --project <项目>
```

从计划里真正听得到的部分出 `plan/subs.srt`。时间按时间线帧率换算（59.94 素材放 29.97 时间线也对）；长句按标点和字数（`--max-chars`，默认 18）拆行；不重叠，每条至少 0.5 秒。whisper 会认错人名地名，**生成后先给用户校对（确认点 3），再进 Resolve**。改错写进 `plan/subs_fixes.json`，每次生成都会套用，改了计划重新生成也不会丢：

```json
{"replace": {"特罗姆色": "特罗姆瑟"},
 "translate": {"How old is this shop?": "这家店开了多久？"}}
```

`replace` 换词，`translate` 按原句整句换。外语句由 agent 翻译写进 `translate`，并标出来请用户校对；还没翻译的会在输出里列出。已有 subs.srt 时不覆盖（可能有手改），重新生成要加 `--force`。whisper 的时间比声音早约 0.25 秒，字幕会稍早于声音出现，这是正常的。

## 7. 用 MCP 搭时间线

前提：
- 用户确认了上一节的剪辑计划，有字幕时也校对过了。建工程、切工程由脚本完成，用户不用操作 Resolve 界面；Resolve 没开时 MCP 会自己启动它。
- Resolve 偏好设置 System > General 里「External scripting using」为 Local。
- 本会话里能看到 MCP 工具。注册 MCP 之后通常要重启 agent 的客户端才能看到。

工具名以 MCP 工具列表为准，下面的调用都在 21.1.1.10 + MCP 4.15.3 上探针过（resolve-routes.md）。每一步都检查返回值，失败不会报错（常见坑第 1 条）。每一步做完都 `save`：Resolve 崩溃后重开不弹窗，但没保存的改动会丢。MCP 的破坏性操作会把当前文件夹留在 Archive 上，append 或导入之前先 `media_pool set_current_folder {path:"Master"}`。

1. **工程。** 先 `save` 用户当前的工程，再按 resolve-routes.md「自动建工程」新建 `vlog-<项目>-v<n>`（试剪用 `vlog-style-QA-<项目>`）：advanced server 生成空 `.drp` → `safe_project_import`（先 dry_run）→ `load` → 马上 `save`。不在用户正在剪的工程上直接改。
2. **设置。** 先删掉模板自带的空时间线（confirm_token 两步），再写帧率：`timelineFrameRate` 只有工程里一条时间线都没有时才写得进去。帧率和分辨率取 `cut_plan.timeline`，色彩管理设为 Rec.709 Gamma 2.4。分辨率本机没探针，写完读回。播放帧率写不进去，进手动收尾清单。这些都要在导入任何媒体之前做完：WAV 会锁定导入时的帧率。然后建主时间线，先把轨道建够：视频轨建到计划里最大的 `track`，再加一条标题轨（V3）；音频轨 A1 对白、A2 音乐床、A3 原始音乐，开了环境声再加 A4。`trackIndex` 超出现有轨道数时不报错，片段会落到别的轨道上（常见坑第 3 条）。
3. **导入。** 用 `paths.py resolve <相对路径>` 现拼绝对路径，导入 `video[].source`（照片条目是 photo_clips.py 做的 `photo.mp4`）、`dialogue[].source`、压低版音乐床（`plan/ducked.json` 里的 `bed`）和原始音乐。绝对路径不写进任何文件。
4. **字幕文件（有 `plan/subs.srt` 时）。** 导入 .srt 后，**在放任何片段之前**用只传 `clip_ids` 的旧式 append 把它放到空的主时间线上，字幕按 .srt 里的时间落位（从时间线起点算的秒数 × 时间线帧率，探针 #15）。带 clipInfos 的 append 放 .srt 会让 Resolve 崩溃。
5. **放片段。** 按轨分开 append，每条 clipInfo 是 mediaPoolItem、`startFrame=src_in`、`endFrame=src_out`、`recordFrame=rec_in`（MCP 的 `record_frame` 默认相对起点）、`trackIndex`：
   - V1 的全部条目一次放，再放 V2（`track: 2` 的 cutaway）。照片条目（`kind: photo`）和视频一样按帧放，`startFrame` 是 0；放好后 `scale` 不是 1 的条目用 `timeline_item set_transform {ZoomX: scale, ZoomY: scale}` 缩小（探针 #14），读回 `get_transform` 核对。`audio: mute` 的条目只放视频（mediaType 1）；没有 story 时 `audio: keep` 的条目照旧视频音频一起放。
   - `dialogue[]` 一次放到 A1，只放音频（mediaType 2），源区间用对白条目自己的 `src_in`/`src_out`，可以和画面不同（J/L cut，探针 #13）。`ambient[]` 同样放到 A4。
   - `speed` 不是 1 的条目 `endFrame` 另算，见第 8 步。先读回时间线起始帧，不要假设从 0 开始。
6. **读回核对，再套字幕样式。** 读回每个条目，视频和音频轨都要，整理成 compare.py 的格式（见下一节），跑 `compare.py readback`。`timeline clip_where` 一次就能读回所有轨道的位置和源帧；59.94 素材读回的 `source_end` 少 1 帧，以 `get_source_end_frame` 为准或留 1 帧容差（常见坑第 20 条）。不通过就删掉这条时间线，查原因后重建新版本。已经放上去的条目无法移动或修剪。

   通过后马上套字幕样式：`timeline_ai set_subtitle_preset`，`preset` 用 `Simple White`，`inputs` 写 font、fontStyle、size、position（数值见风格包 STYLE.md 的字幕换算，框高 ≈ 1.1 × size，中心 y ≈ 0.9875 − position y），`revision_name` 写 `<时间线名>_subs`。没缓存的预设不能 dry run。它导出再导入出一条新时间线，原时间线留着。实测条目位置、源帧、音量、缩放、CDL 都带过去了，但 markers 和 Fusion 合成（标题、推拉）没验证，所以放在这些之前做。之后的步骤都先 `timeline set_current` 到新的那条，并再读回一次。没有字幕时跳过。
7. **转场。** 从最后一条往前，对有 `transition_out` 的条目按 `transition_out.resolve` 的 type 和 category 加转场，传短名，`position:end`、`alignment:center`。返回 `success:false` 多半是余量不够，改成硬切并告诉用户。
8. **变速。** 对 `speed` 不是 1 的条目调 `set_speed {options:{Percentage: speed × 100}}`。改速度不改条目在时间线上的时长，条目从源入点起按新速度播。cut_plan 里的 `src_out` 是放慢后实际播到的位置，所以第 5 步 append 这类条目时 `endFrame` 要按 100% 速度算：`src_in + round((rec_out − rec_in) × source_fps / 时间线帧率)`，素材在 `src_out` 之后要有这么长；读回时长不对就把这条改成 100% 并告诉用户。时间线是 50p 或 60p 时再加 `set_retime optical_flow`。套完在条目首尾导出静帧核对源帧。变速曲线没有可用的自动路线（离线 `.drt` 本机否掉），写进手动收尾清单。
9. **推拉。** 对有 `motion` 的条目，每条写一个 `.comp`（写到项目的 `plan/fusion/<条目 id>.comp`），用 `timeline_item_fusion import_comp {path, track_type:"video", track_index:<条目的 track>, item_index}` 挂上。`N = rec_out − rec_in − 1`，`a = from`，`b = to`；comp 第 0 帧是条目第一帧，末帧关键帧必须放在 N。导出首、中、尾三张静帧核对缩放。模板不写素材路径，MediaIn 自动绑定到条目的素材：

   ```
   Composition {
   	Tools = ordered() {
   		MediaIn1 = Loader { Inputs = { }, },
   		Transform1Size = BezierSpline {
   			SplineColor = { Red = 225, Green = 0, Blue = 225 },
   			NameSet = true,
   			KeyFrames = {
   				[0] = { a, RH = { N/3, a + (b − a)/3 }, Flags = { Linear = true } },
   				[N] = { b, LH = { 2N/3, a + 2(b − a)/3 }, Flags = { Linear = true } }
   			}
   		},
   		Transform1 = Transform {
   			Inputs = {
   				Size = Input { SourceOp = "Transform1Size", Source = "Value", },
   				Input = Input { SourceOp = "MediaIn1", Source = "Output", },
   			},
   		},
   		MediaOut1 = Saver {
   			Inputs = {
   				Index = Input { Value = "0", },
   				Input = Input { SourceOp = "Transform1", Source = "Output", },
   			},
   		},
   	},
   }
   ```

   花括号里的 a、b、N 写成算好的数字。
10. **标题。** `titles[]` 每条单独建一个时间线，在里面 `insert_fusion_title`（`template`），再用 `fusion_comp bulk_set_inputs` 写标题条目 comp 里 `Template` 节点的 StyledText、Font、Style、Size、Center（不用 `set_title_text`：它只是猜着用 SetProperty 写文字，也不管字体）。两种字体（比如衬线时间 + 黑体地名）：在同一个 comp 里 `add_tool` 一个 TextPlus（`Line2`）和一个 Merge，Background ← Template，Foreground ← Line2，MediaOut1.Input ← Merge1。`style.position` 的原点在左上，Fusion 的 Center 原点在左下，写入时 y 要换成 1 − y。回到主时间线，把标题时间线当片段放到 V3 的 `rec_in`，`endFrame` 给 `rec_out − rec_in`，超过 5 秒也行。标题时间线的媒体池条目 id 用 `folder get_clips` 按名字找，`media_pool_item get_timeline` 能确认它是哪条时间线（常见坑第 24 条）。导出静帧核对文字。
11. **调色。** 默认按章套 `plan/grade.json`（§6 算出的）：V1 和 V2 的每个条目，照片也算，用 `items[条目 id]` 找到所属章，调 `timeline_item_color safe_set_cdl`，参数直接用该章的 `resolve`。每次检查返回值。CDL 读不回来，所以套完按 §8 每章导出静帧核对。公式和 Resolve 的一致性目前只验证过 slope，offset、power、sat 待探针（resolve-routes.md 探针 #16），核对结果离预测远时先怀疑这一点，用 `grade.py preview --graded` 比。
    例外：风格包的 `grade.lut`、`grade.drx`、`grade.grain` 有值时，先套这些，再写 CDL。DRX 按这个配方生成：`drx generate` 出一张中性图做底，`drx merge` 把颗粒作为 newNodes 加进去（底图里不要放真实节点，会被有损重写；不要用 `drx lut_apply`，它会丢掉一级调色）。每个条目 `safe_apply_drx` 套一次（套用会替换整个节点图，confirm_token 两步），再 `graph set_lut` 把 LUT 套到节点 1。LUT 先 `lut install` 装进 MCP 子目录并 `refresh_luts`。只有 LUT 时直接 `graph set_lut`。grade.py 从代理算的 CDL 不含 LUT 和 DRX 的效果，所以这时先套好它们，每章导出几张静帧放进 `qa/stills_raw/<章名>/`，再跑 `grade.py solve --project <项目> --stills "$P/qa/stills_raw"`，用新的 grade.json 写 CDL。这几样和 `safe_set_cdl` 叠用时 CDL 落在哪个节点还没探针过，第一次用先在 `vlog-style-QA-*` 里试。
12. **声音。** 压低版音乐床放 A2，原始音乐放 A3 并静音。片段音量用 `timeline_item set_property {key:"AudioVolume", value:<dB>}`，不要用 `set_audio Volume`（它写的是旧键，什么都不改）。片尾淡出等用 `set_fades {options:{FadeIn, FadeOut}}`，整数帧。对白在 A1，音量按条目设。风噪重的对白轨可以开人声分离：`timeline set_voice_isolation_state {track_index:1, state:{isEnabled:true, amount:<0–100>}}`，默认关，开之前先听过或问过用户。
13. **字幕核对。** 字幕样式已在第 6 步套好。静帧里看不到字幕，要烧录渲染一小段（只在 `vlog-style-QA-*` 或用户同意时），用 `vision_ocr` 量框高和中心，和 style.text.subtitle 比；差得多就调 size、position 重新套。这个预设写不了描边（常见坑第 28 条）。导出成片时设 `ExportSubtitle:true`、`SubtitleFormat:"BurnIn"`。
14. **标记。** `markers[]` 打到时间线上；`Timeline.AddMarker` 用相对时间线起点的帧号，直接用 `rec`。第一个标记是手动收尾清单，其中包括手动设置播放帧率。
15. **保存工程。**

## 8. 验收

**读回格式**（compare.py readback 的输入，帧号用绝对帧号，end 不含该帧）：

```json
{"start_frame": 108000,
 "video": {"1": [{"start": 108000, "end": 108090, "source_start": 0, "name": "C0001.MP4"}],
           "3": [{"start": 108600, "end": 108659, "name": "title-t001"}]},
 "audio": {"1": [{"start": 108000, "end": 108300, "source_start": 480, "name": "C0003.MP4"}]}}
```

```sh
python3 -I -B compare.py readback "$P/qa/readback.json" --plan "$P/plan/cut_plan.json" --style <风格>
uv run --python 3.13 --script compare.py look "$P"/qa/stills/<章名>/*.jpg --style <该章 look_ref> \
  --out "$P/qa/look_<章名>.json"   # 每章一次
```

- readback 要求每个计划条目都在、顺序对、帧号对。V2 和 V1 一样按 track 分组；有 `dialogue[]` 或 `ambient[]` 时还要读回 A1、A4，每条的位置和源入点也要对。转场两侧的边允许偏差半个转场长度。镜头时长分布只报告，不判失败。
- 静帧：按章核对。每章挑 2–3 个镜头，把播放头放过去，用 `ExportCurrentFrameAsStill` 导出到 `qa/stills/<章名>/`，用该章的 `look_ref` 跑 `compare.py look`（brief 没写 look_ref 的章用剪辑计划的风格包）。把结果和 grade.json 里该章的 `predicted` 比：两者接近而 look 不通过，是 grade.json 的警告里已经说过的（撞边界、色偏修不到、夜景不提亮），如实告诉用户原因，不再硬调；两者差得远，是公式和 Resolve 对不上，按探针 #16 的方法查。
- 把静帧拼成总览，和参考的总览图（或风格包 `stills/`）并排看一遍，再交给用户。只读回不算通过，清单见 qa.md。

## 9. 导出（只在用户要求时）

先保存工程，再用 `render prepare_delivery_job` 排队：母版用 ProRes 422 HQ，发布用 H.265 Main10。传 `CustomName`，时间线名里有 `/` 或 `:` 会导致渲染失败。用户明确说要开始渲染才开始。

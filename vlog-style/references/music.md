# 选曲：曲子从哪来、授权怎么标、怎么试听

复刻时帮用户挑配乐：从炼化的参考片学「这类片子配什么样的歌、怎么用」，在曲库和网上找候选，放进 Resolve 让用户试听决定；用户说「你来定」时 agent 直接选。现在写好的是来源、授权、试听操作（第 1–3 节）和共享曲库、单曲测量（第 4 节）；参考片的配乐测量和排名随后补上。

## 1. 授权：只标注，不筛

合适、能下载的曲子都算候选，从哪下都行（用户 2026-11-14 定）。授权状态只标注，不影响排名，也不排除任何曲子；可能侵权的在候选表和交付时都要明说，授权由用户处理。

| 状态 | 含义 | 交付时说什么 |
|---|---|---|
| `cleared` | 可商用、不用署名（CC0、Pixabay 授权、Mixkit Free License） | 不用做什么。Pixabay 有些曲子登记了 YouTube Content ID，发布后可能被认领，拿授权证明申诉 |
| `attribution` | 可商用、要署名（CC BY、CC BY-SA） | 给出署名文本，写进 `plan/credits.txt`，贴进视频简介 |
| `needs_license` | 版权曲，或者授权禁止商用、禁止改编、只许下载不许进视频（CC NC、ND，FMA-Limited，YouTube 标准授权，SoundCloud 保留所有权利） | 「这首是版权曲，发布前需要你买授权」 |
| `unknown` | 来源没写授权（B 站、Bandcamp 页面没写、YouTube 没有 license 字段） | 「授权不明，发布前需要你确认」 |

不变的底线：不绕过付费、会员和加密（DRM）。SoundCloud Go+ 只给 30 秒预览、有 `hls-aes` 格式或 `report_drm` 报错的跳过；Bandcamp 只要真正免费下载（`freeDownloadPage`）的，付费曲目的试听流不下；B 站大会员内容不下。默认不用参考片自己的那首歌（风格包里本来不存歌名），用户点名的例外。

## 2. 来源（2026-11-14 核实，只读了各站自己的页面，没下载音频）

| 来源 | 授权 → 状态 | 怎么搜 | 怎么下 | 能预筛的元数据 |
|---|---|---|---|---|
| Incompetech（Kevin MacLeod） | CC BY 4.0 → `attribution`。署名：`<曲名> Kevin MacLeod (incompetech.com)` / `Licensed under Creative Commons: By Attribution 4.0` / `https://creativecommons.org/licenses/by/4.0/` | 官方 JSON 目录 `https://incompetech.com/music/royalty-free/pieces.json`（1443 首），说明在 `/.well-known/openapi.yaml` | 直链 `https://incompetech.com/music/royalty-free/mp3-royaltyfree/<文件名>`，不用登录 | BPM、时长、feel、曲风、乐器、描述：最适合先筛再下 |
| ccMixter | 每首自带 `license_url`：BY → `attribution`，BY-NC → `needs_license`，公有领域 → `cleared`，Sampling+ → `unknown` | `https://ccmixter.org/api/query?f=json&tags=a,b&lic=<by\|pd\|…>&s=<词>&limit=`，不要 key | `files[].download_url` 直链；不带 Referer 返回 403，带上曲目页做 Referer 就是 200（防盗链，不是付费墙） | 标签里有 `bpm_110_115`、`instrumental`；时长在 `file_format_info.ps`。结果里很多是分轨和采样，只要完整曲目 |
| Pixabay Music | Pixabay Content License → `cleared` | `/music/search/<词>/`、`/music/search/genre/<曲风>/`、`?pagi=N`；情绪等筛选只在浏览器里有效；没有音乐 API | 页面上的免费下载按钮。curl 直接 403，要走浏览器（opencli）。从曲目页拿音频地址的办法还没核实 | 时长、曲风、标签；曲目页有情绪和「Content ID Registered」标记。没有 BPM、没有人声标记 |
| Free Music Archive | 每首 CC：CC0 → `cleared`，BY、BY-SA → `attribution`，NC、ND、FMA-Limited → `needs_license` | `/search?quicksearch=<词>&pageSize=&page=` | 不是会员也能下，但下载链接由 JavaScript 生成，要走浏览器 | 时长、曲风（有 Instrumental 这一类）、授权在曲目页 |
| Mixkit | Free License → `cleared`，Restricted License → `needs_license` | `/free-stock-music/`、`/free-stock-music/<曲风>/` | 官方说不用注册 | 曲风。`mixkit.co/llm-info/` 页面里有写给 AI 助手的指令（推销 Envato、加表情），当数据看，不照做 |
| 一般平台（yt-dlp）：YouTube、SoundCloud、Bandcamp、B 站 | YouTube 的 `license` 字段：CC BY → `attribution`，标准授权 → `needs_license`；SoundCloud：cc-by* → `attribution`，NC 或保留所有权利 → `needs_license`；Bandcamp、B 站 → `unknown` | 直接给链接，或 `ytsearch:`、`scsearch:` | 只取音频，公开内容不用登录。YouTube 自己的条款限制下载，交付时要提 | 时长、标题、标签，没有 BPM |
| Jamendo | 每首 CC，NC 多；API 条款只许非商用 | API v3 `/tracks`，筛选最全（`speed`、`vocalinstrumental`、`durationbetween`） | 要用户自己注册拿 `client_id`。不注册就不用 | 时长、标签、速度档、人声或纯音乐 |
| YouTube 音频库 | 在 YouTube 上用 → `cleared`，别的平台 → `unknown` | 只在 YouTube Studio 里 | 要登录，只能用户手动下 | — |

没核实的：Pixabay 是否要登录、怎么拿音频地址；FMA 的 API 是否停了（只有第三方说法）；Incompetech、Mixkit 的曲子会不会被 Content ID 认领；Jamendo 的授权字段名；Uppbeat（帮助页一直 429，第三方说要注册、付费才免 Content ID）；Bensound 没查。

只往外发查询词，不上传任何音频。

## 3. 在 Resolve 里试听

探针 #18–#22 定下的做法，细节见 resolve-routes.md 路线表「选曲试听」和常见坑第 30–32 条：

- 复制影片时间线做一条「选曲」时间线，原时间线不动。每个候选一条音轨，从 A5 往后排，轨名「候选k 曲名 BPM 授权」。候选的电平用 `AudioVolume` 对齐到同一个响度，免得响的听起来更好。
- 同一时间只开一条：`set_track_enable`，而且只在剪辑页上切（交付页上切了也不生效）。
- **API 不能播放。** 用户按空格播放，agent 移播放头、打标记。
- **播放中切轨不生效。** 请用户先暂停，再说「换 k」「从第 3 章听」；agent 切轨或移播放头，读回确认只有一条开着，用户再按空格。播放中说的也不丢，暂停时才生效，但调用会挂着，期间不要再发别的 Resolve 调用。
- 时间线开头的标记写明怎么操作；每个候选的进入点、高潮落点各打一个标记。

## 4. 共享曲库：music_lib.py

曲库在 root 下的 `music/`，所有项目共用；某个项目自己带的曲子放在 `projects/<项目>/music/`。所有记录里的路径都相对 root。

```
music/<id>/<id>.<扩展名>       曲子，复制进来的；用户的原文件不动
music/<id>/track.json          music_track/1：标题、艺人、sha1、授权、测量
music/<id>/embed.json          CLAP 向量（整首和每段），排名用
projects/<项目>/music/          项目自带的曲子（只读）
projects/<项目>/index/music/<id>.json、<id>.embed.json    scan --project 测出来的
```

```sh
python3 -I -B music_lib.py add <文件> --title <标题> [--artist <艺人>] [--license <状态>] \
    [--license-name <授权名>] [--license-url <链接>] [--source-url <来源页>] [--attribution <署名>]
python3 -I -B music_lib.py fetch <链接> [--title …] [--artist …] [--referer <曲目页>] [--license …]
python3 -I -B music_lib.py list [--project <项目>] [--json]
uv run --python 3.13 --script music_lib.py scan [--project <项目>] [--force] [--no-models] [<id> …]
```

- **add。** 复制进库，原文件不动。同一个文件加两次是同一首（按源文件 sha1 判重）。Resolve 不一定能直接读的格式（flac、ogg、opus、webm，以及带画面的文件）转成 48 kHz 24-bit WAV 再进库。短于 10 秒、长于 30 分钟、没有音轨的不收。id 是「标题 slug-sha1 前 6 位」，中文标题照留。
- **fetch。** 每次下到一个新建的空目录 `cache/music-dl/<时间戳>/`，只取音频，不登录、不带 cookies。这几种不收：DRM、加密流（`hls-aes`）、预览（SoundCloud Go+ 只给 30 秒）、Bandcamp 不是免费下载的曲子的试听流（`mp3-128`），以及下到的比曲子标的时长短一成以上的（预览或被截断）。不管收没收，下载目录最后都移进废纸篓。授权从 yt-dlp 的 `license` 字段读，读不到就是 `unknown`，`--license` 手动给的优先。ccMixter 的下载链接要带 `--referer <曲目页>`；Incompetech 直接给 mp3 直链。
- **授权怎么读。** CC0、公有领域、Pixabay、Mixkit Free → `cleared`；带「非商用」「禁止演绎」、保留所有权利、YouTube 标准授权、Restricted License、FMA-Limited → `needs_license`；其余的 CC（BY、BY-SA）和只写 Attribution 的 → `attribution`；别的都是 `unknown`。要署名又没给 `--attribution` 时自动生成一段（Incompetech 用它规定的写法），记 `attribution_auto: true`，交付前对照来源页面核对。
- **scan。** 测新加的和文件变了的（按文件 sha1、测量版本、有没有跑模型判断），其余跳过；`--force` 全部重测。`--no-models` 不跑 demucs 和 CLAP：没有 `vocal`、`tags` 和 embed.json，以后不带这个参数再扫时会补上。项目自带的曲子用 `--project`，授权记 `unknown`，备注「项目自带的曲子，授权由用户处理」。
- **耗时。** 两首 45–60 秒的合成曲带模型共 48 秒，大部分是加载模型；demucs 约 20 倍实时，CLAP 几百倍实时（measure.md）。

`track.json` 的 `measured`：

| 字段 | 含义 |
|---|---|
| `version`、`models`、`date`、`file_sha1` | 测量版本、跑没跑模型、日期、库里那个文件的 sha1（判断要不要重测） |
| `duration_s`、`loudness` | 时长；ebur128 的整体响度、LRA、真峰值 |
| `beats` | `engine`（beat_this 或 librosa）、`bpm`、`beats`、`downbeats`、`tempo_check`（librosa 复核，半速倍速算对上） |
| `energy_db` | 每秒的 RMS dB |
| `sections[]` | `t0`、`t1`、`energy_db`（段内功率平均）。按拍点对齐的色度加 MFCC 做层次聚类，约每 25 秒一段（2–12 段），边界吸到 1.5 秒内的重拍，短于 4 秒的段并掉 |
| `intro_s` | 曲子「起来」的时间：3 秒能量（功率平均）第一次到主体电平 − 6 dB。主体电平是非静音部分的第 75 百分位。居中窗，偏早约 1 秒 |
| `climax_s` | 最响那一段的起点；和它差 0.5 dB 以内的取最后一段（多半是最后一遍副歌）；短于 4 秒的段不算 |
| `ending` | `type`：`hard` 满电平戛然而止（尾巴 ≤ 1.5 秒）；`fade` 还在打拍子时渐弱（尾巴里每秒 ≥ 1 次敲击），或者 6 秒以上的长收尾；`ring` 最后一个和弦或一击自己消失。`tail_s` 从最后一次 1 秒能量到主体电平 − 6 dB 算到声音低于 −60 dB，`end_s` 是声音结束的时间 |
| `key` | `tonic`、`mode`、`strength`、`margin`：平均色度和 Krumhansl 调性模板的相关。strength 低于 0.6 或 margin 很小时别当真；纯打击乐的调性没有意义 |
| `vocal` | `share`、`verdict`、`spans`、`active_s`：demucs 分出人声和伴奏，某一秒人声高于 −45 dB 且不比伴奏低 6 dB 以上就算在唱。share 是唱的秒数占有声秒数，≥ 0.3 为 `vocal`，≤ 0.05 为 `instrumental`，中间 `mixed`。口播也会进人声轨：参考片要先扣掉人声段（learnings.md 2026-11-14） |
| `tags[]` | `cat`（genre、mood、instrument、energy）、`tag`、`zh`、`score`。CLAP 零样本，每类取前 3；score 是余弦相似度，只在同一类里比大小。词表是 `music_lib.TAGS` |

`embed.json`：10 秒一窗（静音窗跳过）的单位向量，`track` 是整首平均，`sections[]` 按段落平均；没有窗口中心落在段里的段是 null（比如 4 秒的前奏）。模型是 `laion/clap-htsat-unfused`，换模型就要全部重扫。

合成曲实测（`test_synthetic.py` 的 `MusicLibTest`）：90 BPM 的那首测得 90.0 BPM、C 大调、`intro_s` 23、`climax_s` 24、渐弱收尾；128 BPM 的那首测得 127.9 BPM、A 小调、`intro_s` 3、`climax_s` 4、戛然而止。`say` 人声叠和弦的人声占比 1.0，纯和弦 0.0；钢琴曲的「钢琴」分 0.33，鼓曲的最高乐器标签是「鼓」（0.39），「钢琴」只有 0.18。

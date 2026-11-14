# 选曲：曲子从哪来、授权怎么标、怎么试听

复刻时帮用户挑配乐：从炼化的参考片学「这类片子配什么样的歌、怎么用」，在曲库和网上找候选，放进 Resolve 让用户试听决定；用户说「你来定」时 agent 直接选。这一篇先写阶段 0 定下的部分（来源、授权、试听操作），曲库、测量和排名随后补上。

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

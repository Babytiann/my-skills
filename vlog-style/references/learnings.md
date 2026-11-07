# 被否掉的尝试和修法

每次试剪、探针或用户反馈否掉了某种做法，都在这里记一条，最新的写在最上面。下次做同类事情之前先翻一遍。

格式：

```markdown
## YYYY-MM-DD 一句话标题
- 场景：哪个风格包 / 项目 / 探针项，Resolve 版本
- 做法：当时怎么做的
- 问题：哪里不对，怎么发现的（读回、静帧、用户反馈）
- 修法：改成了什么，改了哪个文件或参数
```

## 2026-11-07 成片的字幕框高：带逗号的句子框更高、更低
- 场景：探针 #17，`vlog-style-QA-probe17`，Studio 21.1.1.10 + MCP 4.15.3，Simple White、PingFang SC Regular、`size 0.040`、`position [0.5, 0.032]`，H.265 烧录
- 做法：`compare.py final` 取读到的所有句子的框高、中心 y 中位数，和风格包的 `text.subtitle`（links：4.44%、0.955）比，±15%、±0.015
- 问题：5 句里 3 句带逗号（subs.py 保留了 whisper 的半角逗号），框高 5.21–5.60%，不带的两句 4.72%；中位数 5.21%，超了 17%，好片子被判字号不对。参考片留下的 1371、1048、761 条字幕轨迹里都没有标点，两边量的不是同一种句子
- 修法：`check_subs` 只用不带逗号、分号、顿号、括号和 g/j/p/q/y 的句子量框高和中心（`LOW_INK`），一句都没有才全用，问题里注明「每句都有逗号或下伸笔画」。输出多一个 `measured_on`。改完好片子 6 项全过；坏版本各自只挂对应一项。test_tools 加了按实测值写的回归测试，apply.md §7 第 13 步和 §9 跟着改

## 2026-11-07 mix.py master 差 0.1 LU 也让人上限幅器
- 场景：探针 #17，同上，只出音频的渲染两轮
- 做法：真峰值压着上不去时，`apply_master` 只要比 `--lufs` 少一点就报「差多少 LU，在 Fairlight 主母线上加限幅器（手动收尾）」
- 问题：第二轮实测 −14.1 LUFS、真峰值 −1.0，差 0.1 LU，也报了要上限幅器。`compare.py final` 允许 ±1 LUFS，这点差没有意义，还会让人白做一步手动收尾
- 修法：差 0.5 LU 以上才报（`SHORT_LU`），`expected` 照样写实际能到的值。test_tools 加了 −14.1、−14.4 不报、−14.6 报的回归测试，apply.md §7 第 12 步跟着改

## 2026-11-07 CDL 和 LUT 写在同一个节点，CDL 跑到了 LUT 前面
- 场景：探针 #16，`vlog-style-QA-probe2`，Studio 21.1.1.10 + MCP 4.15.3，逐通道的测试 LUT
- 做法：apply.md 的例外原来写的是先 `graph set_lut` 到节点 1，再 `safe_set_cdl`，NodeIndex 照默认写 1；探针 #9 的 DRX 配方也是 LUT 套到节点 1
- 问题：静帧对上的是「先 CDL 后 LUT」（平均差 0.30/255），和「先 LUT 后 CDL」差 3.5/255。可 `--stills` 是在套好 LUT 的静帧上解 CDL，假设的是 LUT 在前。另外重套 DRX 之后，已写的 CDL 没了，静帧和未调色的逐像素相同
- 修法：中性 DRX 再 merge 一个空节点，LUT 放节点 1，CDL 放节点 2，静帧对上「先 LUT 后 CDL」（0.25/255）。`grade.py solve` 加 `--node`（默认 1），例外时传 2，`resolve` 块里的 NodeIndex 跟着变。只有 LUT 时也建两个节点；CDL 一定在 DRX 之后写。写进 apply.md §7 第 11 步、resolve-routes.md 路线表和常见坑第 29 条

## 2026-11-07 按章调色不走 DRX，CDL 用 safe_set_cdl 直接写
- 场景：阶段 3 按章调色，离线 server `drx cdl_io`，MCP 4.15.3（读源码，没探针）
- 做法：需求文档原来的写法是每章生成一个 DRX：`drx generate` 中性打底，`drx cdl_io` 把 CDL 换成 DRX 节点再 `drx merge`，每个条目 `safe_apply_drx`
- 问题：`cdl_io` 把 slope 当 gain、offset 照搬、power 当 gamma、sat 从 0–2 换成 0–100，源码注释自己写着是近似换算。grade.py 按 ASC CDL 公式预测的 look 和渲染出来的会差一截，还得另做补偿。探针 #9 里的「CDL」节点用的也是 gradeParams 的 UI gain 和 saturation，不是 ASC CDL
- 修法：grade.py 输出精确的 ASC CDL 和现成的 `safe_set_cdl` 参数（`resolve` 块），每个 V1/V2 条目写一次。这也是片场按场出 CDL、调色师套用再细修的常见做法。#13 验证过 slope，#16 验证过 offset、power、sat 和分通道组合。DRX 只留给风格包自带的 LUT、DRX、颗粒（apply.md §7 第 11 步的例外）

## 2026-11-07 照片的拍摄时间不能用 sips 和 Spotlight 读
- 场景：阶段 2 照片，macOS 自带的 sips 和 mdls
- 做法：打算用 `sips -g creation` 或 `mdls kMDItemContentCreationDate` 拿拍摄时间，免得自己解析 EXIF
- 问题：sips 的 `creation` 只读 TIFF 的 DateTime，相机写的 DateTimeOriginal 读回 `<nil>`；mdls 给的是这份文件的创建时间，复制一次就变成复制的时刻。EXIF 的时间本身不带时区，要另读 OffsetTimeOriginal。用户发来的一张实拍（经聊天转发）只剩 OffsetTimeOriginal、镜头和亚秒，拍摄时间和机型都被去掉了
- 修法：sips 生成预览时会保留 EXIF，`prep.py` 用标准库从 preview.jpg 读 DateTimeOriginal 和 OffsetTimeOriginal，HEIC 也一样。没有时区的按本机时区读，设备名加「(no time zone)」，好单独设时钟偏移；没有拍摄时间的照片会提醒换原片或在 tags.json 写 `shot_at`。方向用 EXIF Orientation，ffmpeg 9 解 JPEG 时自己转正，sips 转 HEIC 也保留这个标签

## 2026-11-07 整条片一次转写，prompt 只管得到第一个窗口
- 场景：测试 A，孤岛（BV1uparzhEjs，29 分钟），mlx-whisper large-v3-turbo，`measure_audio.whisper()` 原来的写法
- 做法：整条声音一次 `transcribe`，`initial_prompt` 写简体和标点，`condition_on_previous_text=False`，把 whisper 的 segment 当句子用
- 问题：mlx_whisper 在每个 30 秒窗口之后重置 prompt（`prompt_reset_since`），第一个窗口之后就没有标点，也不保证简体。1121 段里只有 24 段带句末标点，37% 的字幕行被切进两段，segment 中位数 1.0 秒，只是半句；另有 462 个边界附近没有字幕行（音乐段里的幻听）。打开 `word_timestamps` 也一样，而且 93.5% 的词间隔是 0，靠停顿切不出句子
- 修法：`measure_audio --sentences`：在静处把声音切成不到 30 秒的段，每段都带 prompt 和词时间戳转写，再按句末标点、长停顿、换语言拼成句子（`stats.quiet_cuts`、`stats.sentences`）。句末标点 95%，被切开的字幕行 6%，29 分钟音频 231 秒。apply 默认加，炼化不加，输出不变

## 2026-11-07 自动判语言时，中文 prompt 把外语转成了中文
- 场景：同上，`--language auto`
- 做法：每段自动判语言，prompt 照旧用中文那句
- 问题：判成英语的 45 句文字全是中文，而且是重复的粗糙翻译（「全部都在我的手」连说三遍）。写死 `zh` 时，外语对话也会变成这种乱码
- 修法：判成非中文的段去掉 prompt，按判出的语言再转一次（整条只多 2 秒），101 句英语都是原文。语言作为第 4 项写进 footage.json，字幕由 agent 翻译，写进 subs_fixes.json 的 `translate`。一段里混着两种语言时仍然只按一种转，apply.md 写明了

## 2026-11-07 字幕字号按笔画高度估，烧出来偏大
- 场景：探针 #15，`vlog-style-QA-probe2`，Studio 21.1.1.10 + MCP 4.15.3
- 做法：links 和三个子包的 STYLE.md 按 polar-night 那次探针的比例估算，写的是 `size 0.053` + `position [0.5, 0.023]`
- 问题：风格包的字高是 `vision_ocr` 的框高，polar-night 那次量的是笔画。PingFang SC 按 0.053 渲染，OCR 框高 6.1%，比目标 4.4% 大四成。另外 `set_subtitle_preset` 不给预设名直接拒绝，也写不了描边
- 修法：两次渲染拟合出「框高 ≈ 1.1 × size」「中心 y ≈ 0.9875 − position y」，`size 0.040` + `position [0.5, 0.032]` 实测框高 4.44%、中心 y 0.9555。各 STYLE.md 的换算行和 resolve-routes.md 常见坑第 28 条改过，预设用 `Simple White`

## 2026-11-07 原生静图按帧放不出想要的长度
- 场景：探针 #14，同上
- 做法：jpg、png、heic 直接导入，append 时 `end_frame` 给 120、240、72
- 问题：读回三条都是 150 帧，即偏好设置里默认的静图时长 5 秒，`start_frame`/`end_frame` 不起作用。改偏好设置要先问用户，而且照片长短不一，一个默认值也不够
- 修法：照片先用 ffmpeg 做成时间线帧率、等比缩放、标 bt709 的短片再按帧放，75 帧和 240 帧都对，缩放生效，色差可以忽略。写进 resolve-routes.md 路线表「照片」和常见坑第 27 条。同一轮实测还发现常见坑第 24 条过时了：`folder get_clips` 会列出时间线条目的 id

## 2026-11-06 素材不够时只扣分，会把后几天的事搬到前面
- 场景：规划 plan_cut 按拍摄时间选镜头，用户评审计划。需求文档原来写的是往回选扣分、不得不往回选时给 warning
- 做法：往回选的镜头只扣分；素材不够时 `any_tags` 回退到全部素材（`plan_cut.py` 原来的写法）
- 问题：第一天素材不够时，第三天标签和画面都更好的镜头会被拿来填，检查还能通过。用户：「三天后的素材……放到第一天来，那这是完全不对的」。改成一律禁止又太死：「单纯的风景运镜，必要时还是可以的」
- 修法：镜头分两类。默认都有时间进展，不能离开本章的时间窗；tags 里标了 `movable` 的纯风景，在同地点、光线相容时才能借，借了在表里标「借用」。还不够就报错，写明差多少秒，不悄悄回退。例外（预告、片尾照片、赞助段、`out_of_order`）要列名，`check_plan` 再独立查一遍

## 2026-11-06 `trash --all` 会带走还没测完的参考
- 场景：第三条还在跑 OCR，另一个会话在规划清理 links 的下载缓存
- 做法：`cleanup.py trash` 只靠 measure_video.json 判断要不要先 keep
- 问题：还没跑完 measure_video 的参考没有这个文件，保护不起作用，`trash --all` 会把它和别的缓存一起移走，正在跑的任务就断了。按 id 清也一样，只是不容易点错
- 修法：`trash` 默认挡住「没有 labels.json 也没有保留副本」和「有进程正在用」的参考，`--all` 跳过并列在 stderr，按 id 时拒绝。有意放弃的下载加 `--unfinished`；正在用的一律不动。`list` 加了 `state`。有保留副本的回看下载不算没测完

## 2026-11-06 博主级 draft 能跑通，但有几项不能直接用
- 场景：links 博主级包，三条真实参考第一次合并
- 做法：直接用 `aggregate.py draft` 的结果
- 问题：`summary` 是空的；`grade.summary`、`audio.mood`、`selection.opening` 只取单条；`text.title` 的位置和 `offset_s` 按标题个数算，一条片子的 7 个标题压过另外两条，`offset_s` 成了 34.78 s（plan_cut 会把标题推迟到段落开始后这么久）；`grade.look` 带着三条相差很大的色偏；`transitions` 带着 `other`（拍摄时做的手挡镜头），plan_cut 按占比可能抽中，Resolve 又搭不出来
- 修法：逐项手改，写进 links/STYLE.md 的炼化记录。标题的位置和时机取每条视频的中位数；look 只留三条一致的项；`other` 从 `transitions` 去掉，计数留在 evidence。写下一个博主级包时照这个清单过一遍

## 2026-11-06 STYLE.md 里顺手写的数和定性说法没有对过测量
- 场景：links/first-snow-mountain 和 links/kula-kangri 写完之后复核
- 做法：照上一条的模板写，「多数」「大部分」这类话凭判读时的印象
- 问题：「地图多数用 15–20 帧叠化进出」实际是 9 个里 5 个、14–20 帧；「对话撑起大部分时长」实际中景按时长占 49.9%；kula 写的「185 个稳定前进或后退」实际是 104
- 修法：写完用脚本按 qa.md 逐条核，尤其是「多数」这类词，能换成数字就换

## 2026-11-06 下载超过 60 秒，opencli 就报失败，文件其实是完整的
- 场景：links/first-snow-mountain（55:12，1.1 GB），`prep.py ref` 调 `opencli bilibili download`
- 做法：只按 opencli 的退出码和留下的文件判断下载成败，报错时只显示 stderr
- 问题：两次都报 download failed，stderr 结尾却是 `Download complete`，两次的文件逐字节相同，完整解码 0 错误。opencli 的浏览器命令默认 60 秒超时（`OPENCLI_BROWSER_COMMAND_TIMEOUT`），超时只报错、不中止下载，错误打在 stdout 上。前两条下载 50–58 秒没碰到，这条要 106–132 秒
- 修法：`prep.py` 下载时把这个超时设成 `DOWNLOAD_TIMEOUT_S`（3600 秒），用户自己设过的值优先；失败时报退出码，stdout 和 stderr 都带上。重下一次退出码为 0

## 2026-11-06 OCR 读到的 `/bpm` 被 paths.py check 当成绝对路径
- 场景：links/kula-kangri keep 之后跑 `paths.py check`
- 做法：JSON 里凡是以 `/` 开头的字符串都算绝对路径
- 问题：片子里指夹血氧仪外壳上印的 `PR /bpm`，`/bpm` 被 OCR 读成了两条标题轨，在 measure_video.json 和 labels.json 里一共报了 6 个绝对路径，其实一个也没有
- 修法：OCR 文字字段（`text.tracks`、`text.transcript`、`strips.titles`、`titles`、`samples` 的 `text`）以 `/` 开头、又不含真实文件系统根目录的值，`check` 只列成提示，不计数。其他字段照旧

## 2026-11-06 字幕被 OCR 判成标题，keep 之后原文还留着
- 场景：links/kula-kangri，`cleanup.py keep` 之后查保留的测量里有没有字幕原文
- 做法：只拿 kind 为 subtitle 的轨去搜
- 问题：keep 只清 kind 不是 title 的轨。T2357 是一行字幕，OCR 判成了 title，判读时标了 other、note 记 subtitle_misread，14 个字在 measure_video.json 和 labels.json 里共留了 3 处
- 修法：判读时把误判成标题的字幕记成 note `subtitle_misread`，keep 之后按这个 note 把保留副本里的对应文字清空，再拿缓存里的原文搜一遍，搜不到才算过

## 2026-11-05 PySceneDetect 和 ffmpeg 的帧号在长片上会错开
- 场景：links/lonely-house（29:22），第一次在长片上跑 measure_video
- 做法：把 PySceneDetect 给的帧号直接和 ffmpeg 管道里的帧号比较
- 问题：ffmpeg 的 rawvideo 管道是恒定帧率，时间戳跳的地方会重复一帧；PySceneDetect 用 OpenCV，只数解码出来的帧。代理约每 240 s 跳一帧，全片错开 7 帧。结果两路一致的剪辑点只有 116 个，否掉 147 个 psd 剪辑点，多出 29 个 0.5 s 以下的重复镜头。从碎镜头数量异常发现，比对两路的帧号确认
- 修法：`measure_video.py` 用 `proxy_clock` 读代理的时间戳，`stats.cfr_index` 把 PySceneDetect 的帧号映射到恒定帧率时钟，错开的帧数记进 `cuts.psd_clock_shift`。重测后一致 296 个，否掉 0 个，碎镜头 5 个

## 2026-11-05 投稿列表不等于本人投稿：入池前逐条核对 mid
- 场景：给 Links（mid 3816626）建博主专辑，`opencli bilibili user-videos` 列候选
- 做法：先按投稿列表挑候选
- 问题：列表里有别人的合作投稿。逐条查元数据才发现《去了一趟西班牙2.0》《和Links爬了一趟雪山，我悟了。》的 UP 主是影视飓风（mid 946974）
- 修法：入池前用 `opencli bilibili video` 逐条核对 mid，别人的视频不进专辑，喜欢的话放进 misc，出处写真实的 UP 主。`aggregate.py draft` 合并多个 mid 时打印警告

## 2026-11-05 先清缓存丢了测量：改成先 keep 再 trash
- 场景：把 polar-night 搬进 `styles/links/`，想把它并进博主级包
- 做法：炼化确认后直接 `cleanup.py trash`，缓存整个移进废纸篓
- 问题：measure_video.json、measure_audio.json、labels.json 都在缓存里，一起进了废纸篓。`<root>/cache` 已经不存在，要补参考只能去废纸篓找，找不到就得重新下载重测
- 修法：新增 `cleanup.py keep <id> --style <博主|misc>`，把剥掉转写和字幕原文的测量留到 `<root>/styles/<博主|misc>/refs/<id>/`。`trash` 拒绝移走有测量却没 keep 过的参考，确实不留的加 `--no-keep`

## 2026-11-05 「复刻时不看原片」写成禁令太死：改成默认加例外
- 场景：给每个包写出处时，规定 agent 什么时候可以回看参考片
- 做法：写成「出处只用来人工回溯。复刻时不打开链接、不下载、不看视频」
- 问题：用户反馈写得太死。包里没写清或标了「存疑」的项、用户要求对照、refine 补测，这些时候确实要回看
- 修法：默认只靠风格包本身，列出三种可以回看的情况；先查本机的 `refs/` 和 `stills/`，结论写回 STYLE.md，标日期和「回看补充」。见 SKILL.md 第 5 节

## 2026-11-02 离线 .drt 做变速：速度全错，变速曲线改手动
- 场景：Resolve 探针第 10 项，工程 vlog-style-QA-probe（29.97），Resolve Studio 21.1.1.10 + MCP 4.15.3
- 做法：advanced server `capture_media_template` + `assemble` 生成 5 段的 `.drt`（100%、50%、定格、倒放、1→0.5 线性变速），`import_timeline_checked {background:true, require_temp_path:false}` 导入，媒体 6/6 链上，后台 0.7 秒，无弹窗
- 问题：读回、静帧、渲染三方对得上，速度却全不对：100% 段实际 125%、50% 段 62.5%（`.drt` 模板是 24 fps，套进 29.97 工程）；定格停在第 47 帧（应在 120 附近）；倒放卡在第 0 帧；线性变速成了 1132.96% 的恒速。导入的时间线播放帧率是 24，起始帧 86400 却显示 00:59:56:12，MCP 按名义 30 fps 换算时间码，seek 落到片段外，只能按 帧/30 算时间码去 seek
- 修法：慢动作、定格、倒放只走实时 `set_speed {options:{Percentage}}`（探针逐帧核对过）；变速曲线改成手动，`aggregate.py` ROUTES 的 ramp 改 manual，resolve-routes.md 路线表同步。24 fps 工程里恒速会不会对没测

## 2026-11-02 调色 DRX：lut_apply 丢一级调色，merge 有损重写底图
- 场景：Resolve 探针第 9 项，21.1.1.10 + MCP 4.15.3
- 做法：用 advanced server `drx lut_apply` 把 LUT 加进带 CDL 的 DRX；`drx merge` 时把带 CDL 的 DRX 当底图
- 问题：`lut_apply` 的版本套上后，静帧里 CDL 的增益没生效（红色该压暗却还在 245），`probe_node_graph` 读回节点 1 只剩 LUT，一级调色丢了。merge 会重新编码底图的节点：饱和度 0.7 变成 0.014（静帧近灰偏蓝），OFX 参数也被丢掉，只有新加的节点保真。`drx parse` 解析 lut_apply 的输出时还把 server 弄崩了
- 修法：中性 `drx generate` 打底，CDL 和颗粒全放 merge 的 newNodes，`safe_apply_drx` 套上后再用实时 `graph set_lut` 把 LUT 套到节点 1。静帧实测 CDL、颗粒、LUT 三个节点都生效。写进 apply.md 第 7 节第 11 步和 resolve-routes.md 常见坑第 19 条

## 2026-11-02 Fusion 推拉的 .comp：advanced server 生成的不能用
- 场景：Resolve 探针第 6 项，21.1.1.10 + MCP 4.15.3
- 做法：用 advanced server `fusion generate` 生成 MediaIn → Transform → MediaOut 加 Size 关键帧的 `.comp`
- 问题：写出的关键帧格式不对
- 修法：手写最小模板（不写素材路径，MediaIn 自动绑到条目素材，末帧关键帧放在时长 − 1），`timeline_item_fusion import_comp` 挂上，静帧和渲染都是 1.00 / 1.15 / 1.30。模板写进 apply.md 第 7 节第 9 步。另外：实时 `fusion_comp` 搭节点在本机也能渲染，之前「AddFusionComp 不渲染」多半是 comp `Lock()` 期间写值不进渲染的问题

## 2026-11-02 片段音量：MCP 的 set_audio Volume 什么都不改
- 场景：Resolve 探针第 12 项，21.1.1.10 + MCP 4.15.3
- 做法：`timeline_item set_audio {Volume:-6}`
- 问题：返回 `Volume:false` 和一条 known_limitation，`AudioVolume` 读回还是原值。MCP 写的是旧键，它的 known_limitation 是 21.0.2 上记的，过时了
- 修法：用 21.1 文档里的条目属性 `set_property {key:"AudioVolume", value:-12}`，读回 −12，只出音频渲染实测稳态 −12.00 dB；音频 `set_fades` 也能用

## 2026-11-02 带位置参数 append .srt：Resolve 崩溃
- 场景：Resolve 探针第 8 项，21.1.1.10 + MCP 4.15.3
- 做法：`append_to_timeline` 用 clip_infos（带 record_frame、track_index）放 .srt
- 问题：Resolve 直接崩溃。重开后 `project_manager load` 没弹窗，崩溃前没保存的改动丢了
- 修法：先用只传 `clip_ids` 的旧式 append 把 .srt 放到空时间线上，再放视频；每一步做完都 save。烧录用导出设置 `ExportSubtitle:true` + `SubtitleFormat:"BurnIn"`，渲染核对过；静帧里不显示字幕

## 2026-11-01 用视差比区分数字推拉和机位运动：分不开
- 场景：炼化 BV1sHqPBLExZ，measure_video.py 的推拉判定
- 做法：对推拉镜头算「仿射拟合残差 / 光流幅度」，想用视差大小区分剪辑加的推拉（无视差）和机位前移（有视差）
- 问题：手标 20 个镜头对照，两类的视差比重叠，高空航拍前飞几乎没有视差；残差还受人物走动、雪地低纹理影响
- 修法：测量只负责「稳不稳」（`zoom_eff` ≥ 0.95，排除人走过、转身造成的假推拉）；数字还是机位由 agent 看 `strips/moves_*.jpg` 判，aggregate 只按 digital 算推拉比例

## 2026-11-01 黑场判定只看两侧基线：极夜片里大量误报
- 场景：同上，measure_video.py 的 luma_event / snap_cuts
- 做法：最低亮度 < 0.06 且两侧基线中位数 > 0.12 就算黑场，剪辑点挪到黑场中点
- 问题：13 个黑场候选里 6 个是硬切进暗镜头，剪辑点被挪偏 10–12 帧；黑底字卡两头的硬切被挪到一起，字卡从 23 帧变成 5 帧。看事件条发现
- 修法：黑场、闪白在 0.4 秒窗口内两头都要回到基线才算；snap 每次从 `cuts[].raw` 算起。test_synthetic.py 加了 LumaEventTest

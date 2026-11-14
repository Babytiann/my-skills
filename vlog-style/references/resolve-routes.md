# Resolve 实施路线

本机环境：DaVinci Resolve Studio 21.1.1.10，加上 samuelgursky/davinci-resolve-mcp v4.15.3。它注册成两个 server：

- `davinci-resolve` 是实时 server，直接驱动正在运行的 Resolve。
- `davinci-resolve-advanced` 是离线 server，生成 .drp、.drt 和 .drx 文件，不需要打开 Resolve。

下文的工具名取自 MCP 的文档和源码，并在本机探针里实际调用过；MCP 升级后以工具列表为准。

「状态」一列的含义：
- **已验证**：本机探针导出静帧或渲染核对过，或者 MCP 贡献者在 21.1.0.x 上渲染核对过（「本机」一列写明是哪种）。
- **待探针**：本机还没跑过。
- **手动**：没有可用的 API，只能手动做。

「本机」一列是在 21.1.1.10 + MCP 4.15.3 上跑探针的结论，括号里是探针序号：#1–#12 是 2026-11-02 跑的，#13–#17 是 2026-11-07 跑的。本机否掉的路线已经换成备选，`style.json` 里的 `resolve[].status` 和 `aggregate.py` 的 ROUTES 同步改过。Resolve 或 MCP 升级后要重跑探针。

## 路线表

| 风格元素（route） | 做法 | 状态 | 本机 |
|---|---|---|---|
| 剪辑点、B-roll、J/L cut、音乐床（live_api） | `append_to_timeline`，传 clipInfos 一次放好。`startFrame`/`endFrame` 是源素材帧号，从 0 开始，按素材自己的帧率算，end 不含该帧；`record_frame` 相对时间线起点。有对白时画面和声音分开放：V1、V2 传 `media_type 1`，A1 传 `media_type 2`，两边的源区间可以不同，J/L cut 就靠这个。每次 append 之后都读回 | 已验证 | 通过（#2、#3、#13）：入出点逐帧一致，静帧对得上；只放音频的条目源区间比画面提前 2 秒，渲染出来的声画偏移和计划一致；非零起始时间码的素材、单声道素材放立体声轨都没问题。混帧率素材的源出点读回见常见坑第 20 条 |
| 叠化（live_api） | `add_transition` Cross Dissolve，`position:end`、`alignment:center`。两侧素材都要有余量。从时间线末尾往前加，以免条目序号漂移 | 已验证 | 通过（#4）：时长 12 帧跨剪辑点前后各 6 帧 |
| 黑场过渡、Smooth Cut（live_api） | `add_transition`：Dip To Color Dissolve / Smooth Cut，传短名 | 已验证 | 通过（#4）：黑场静帧平均亮度 10.6。10 帧的 Dip To Color Dissolve 在成片里被切点检测读成两个渐变切点（13.18、13.28 s，#17），都在计划切点 ± 半个转场以内 |
| 闪白、甩镜、急推、抖动转场（fusion） | `add_transition`，category 设为 `fusion`，传短名：Brightness Flash、Pan Left/Right、Crash Zoom、Camera Shake | 已验证 | 通过（#4）：闪白静帧平均亮度 225.5，其余三种静帧都能看到效果 |
| 划像（live_api） | `add_transition`，具体名字没试过 | 待探针 | 未探针 |
| 恒定慢动作（live_api） | `set_speed {options:{Percentage}}`，传百分数（50 就是 50%）。60p 素材放在 30p 或 24p 时间线上，50% 或 40% 的慢动作最干净。60p 时间线还要 `set_retime` 设为 `optical_flow` | 已验证 | 通过（#10）：条目时长不变，从源入点起按新速度播，静帧逐帧对上。`optical_flow` 读回 process 3 |
| 定格、倒放（live_api） | `set_speed` 设为 0 或 −100。只走实时 API | 已验证 | 通过（#10）：倒放从源出点往回放；定格停在条目源区间的末尾（不是入点） |
| 照片（live_api） | 不放原生静图（见常见坑第 27 条）。`photo_clips.py` 按 cut_plan 先把照片做成短片：时间线帧率、等比缩放进时间线尺寸（不裁切，留黑边）、显式标 bt709，再按帧 append。HEIC 先用 `sips` 转 jpg，不动原图。缩小放在黑底上用 `set_transform` | 已验证 | 通过（#14）：短片按帧放，75 帧和 240 帧读回都对；ZoomX/Y 0.8 生效，画面 770×576；和原生静图的平均色差 0.81（0–255） |
| 变速曲线（manual） | 在 Resolve 里手动拉变速曲线，打标记写进手动收尾清单 | 手动 | 离线 `.drt` 本机否掉（#10）：速度全错，见 learnings.md 2026-11-02 |
| 静态推镜（live_api） | `set_transform` 设置 ZoomX/ZoomY。4K 素材放在 4K 时间线上，推镜就是放大，会损失清晰度 | 已验证 | 通过（#5）：1.3 倍，静帧比对误差最小 |
| 动态推拉、Ken Burns（fusion） | 每个条目一个最小 `.comp` 模板（MediaIn → Transform → MediaOut，只在 Size 上打关键帧，不写素材路径），填好起止缩放和末帧，用 `timeline_item_fusion import_comp` 挂上。不要用 Dynamic Zoom，它的属性名在 API 定义和 MCP 里对不上 | 已验证 | 通过（#6）：静帧和渲染都是 1.00 / 1.15 / 1.30。实时 `fusion_comp` 搭节点也能渲染，见常见坑第 4 条 |
| 持续抖动（offline_drx） | 把 ResolveFX Camera Shake 节点用 `drx merge` 并进 look DRX | 已验证 | 通过（#9）：节点读回「OFX: 摄影机晃动」，静帧有约 1.05 倍放大和漂移 |
| 遮幅（live_api） | `set_output_blanking` | 已验证 | 未探针（贡献者在 21.1 上验证过） |
| 地点标题、章节卡（live_api） | 每条文字单独建一个时间线，在里面 `insert_fusion_title`，再用 `fusion_comp bulk_set_inputs` 写 Template 节点的 StyledText、Font、Style、Size；两种字体就再加一个 Text+ 和 Merge。把这个时间线当片段放到 V3。同一个标题时间线放多次时文字是共用的，所以每条不同的文字各建一个。标题之间不能重叠 | 已验证 | 通过（#7），换了写字路线：`set_title_text` 只是猜着用 SetProperty 写文字，也不管字体。append 时 end 给大一些就能超过 5 秒 |
| 字幕（live_api） | 导入 .srt，先用只传 `clip_ids` 的旧式 append 放到空时间线上，再放视频。.srt 的时间是从时间线起点算的秒数，Resolve 按「秒 × 时间线帧率」取整成帧。`set_subtitle_preset` 要给预设名（`Simple White` 最接近 Links 的白字），会新建一个时间线修订，之后的步骤都在新的那条上做。导出时 `ExportSubtitle:true` + `SubtitleFormat:"BurnIn"` 烧录 | 已验证 | 通过（#8、#15）：烧录渲染出来了，不写旁挂文件；第 1 秒的字幕落在第 30 帧，第 300 秒的落在第 8991 帧，没有累积漂移。字号、位置和描边见常见坑第 28 条，带位置参数 append .srt 会让 Resolve 崩溃，见第 21 条。H.265 成片（#17）：5 句都读到，出现比 .srt 晚 0.03–0.17 s；关掉 `ExportSubtitle` 再渲一次，一句都读不到 |
| 按章调色（live_api） | 默认路线。`grade.py solve` 每章解一个 ASC CDL 写进 `plan/grade.json`；V1、V2 的每个条目按 `items` 找到所属章，调 `timeline_item_color safe_set_cdl`，参数直接用该章的 `resolve`（`{NodeIndex:"1", Slope:"r g b", Offset:"r g b", Power:"r g b", Saturation:"s"}`，值都是字符串）。返回的 verification 是 unverified，CDL 读不回，只能导出静帧核对。不用 `drx cdl_io` 把 CDL 换成 DRX：它把 slope 当 gain、power 当 gamma、sat 换成 0–100，源码自己注明是近似 | 已验证 | 通过（#13、#16）：slope 1.2、offset ±0.03、power 0.85、sat 1.25 和四项分通道一起，静帧和 `grade.apply_cdl` 的预测每通道平均差 ≤ 0.36/255，各键 look 差都在下限以内。换成 Rec.601 亮度系数或先 power 后 slope/offset，差会到 1.0–3.6，这张测试卡分得出来 |
| 风格包的 LUT、颗粒（offline_drx，例外） | 风格包的 `grade.lut`、`grade.drx`、`grade.grain` 有值时才走。中性 `drx generate` 打底，`drx merge` 加 [空的 CDL 节点, 颗粒]（没有颗粒就只加空节点），`safe_apply_drx` 每段套一次，再用 `graph set_lut` 把 LUT 套到节点 1，最后 `safe_set_cdl` 把 `grade.py solve --node 2` 的 CDL 写到节点 2。同一个节点里 Resolve 先算 CDL 再算 LUT，CDL 写在节点 1 就跑到了 LUT 前面，和 `--stills` 解出来的不一样。只有 LUT 时也这样建两个节点。LUT 先用 `lut install` 装进 MCP 子目录并 `refresh_luts`。套用 DRX 会替换整个节点图，所以只能套一次。SetCDL 写进去读不回来，只能导出静帧核对。颗粒可调的参数：GrainStrength、GrainMean、GrainSkew、Opacity | 已验证 | 通过（#9），换了路线：`drx lut_apply` 会丢掉一级调色，`drx merge` 的底图里放真实节点会被有损重写。节点顺序（#16）：LUT 在节点 1、CDL 在节点 2 时静帧对上「先 LUT 后 CDL」（平均差 0.25/255），两者同在节点 1 时对上「先 CDL 后 LUT」 |
| 光晕、漏光（fusion） | 光晕用 Fusion Glow。漏光素材由用户提供，叠在 V2，`set_composite` 的 CompositeMode 传 5（滤色） | 滤色已验证；Glow 待探针 | 滤色通过（#11）：读回 5，静帧和滤色混合对上。Glow 未探针 |
| 人声分离（live_api） | `timeline set_voice_isolation_state {track_index, state:{isEnabled, amount}}`，按轨生效。默认关，只给风噪重的对白轨开 | 已验证 | 通过（#13）：读回 on、100；渲染里非人声的合成提示音从约 −10 dBFS 降到 −57 至 −75 dBFS |
| 音乐压低（offline_ffmpeg） | 用 `duck.py` 渲染一条只有音乐的压低版音乐床，放在 A2；原始音乐放在一条静音的 A3 上，方便手动重混 | 离线可用（测试实测 −12 dB ±0.5） | 不涉及 Resolve |
| 片段音量（live_api） | `timeline_item set_property {key:"AudioVolume", value:<dB>}`，范围 −100 到 30。不要用 `set_audio Volume` | 已验证 | 通过（#12）：读回 −12，渲染实测稳态 −12.00 dB。整片电平（#17）：`mix.py dialogue` 的 +7.6、−0.3 写进去后，只出音频的渲染里两段对白 −15.8、−16.0 LUFS（目标 −16）；`mix.py master` 一轮 +2.3 dB，第二次渲染 −14.1 LUFS、真峰值 −1.0 dBTP，和预测一样，`master_db` 不再变 |
| 淡入淡出（live_api） | `set_fades {options:{FadeIn, FadeOut}}`，整数帧。片尾淡出也用它，不用转场：最后一个片段后面没有片段，单边转场会出错。片头淡入也可以在第一个片段上加 Cross Dissolve，`position:start`、`alignment:right` | 已验证 | 音频通过（#12）：渲染里淡入 1 秒、淡出 2 秒都在。视频 set_fades 本机没单独测；片头 Cross Dissolve 淡入通过（#4） |
| 选曲试听：候选音轨（live_api） | 每个候选一条音轨，A5 起往后排：`add_track audio {audio_type:"stereo"}`（包在 `timeline_versioning begin_run`/`end_run` 里，只归档一次），`set_track_name` 写「候选k 曲名 BPM 授权」。候选按位置 append（`media_type 2`），`AudioVolume` 对齐响度，`set_fades` 淡入淡出。只开一条：`set_track_enable {track_type:"audio", index, enabled}`（参数叫 `index`，不是 `track_index`）。切换前 `resolve_control get_page` 必须是 `edit`，用户要先暂停。`set_clip_enabled` 也能静音，作备用 | 已验证 | 通过（#18–#22）：中文轨名原样读回；record_frame 150 加源 60–1060 读回逐帧一致；三条候选 0 / −6 / +6 dB 后渲染都是 −20.0 dB；关掉的轨渲染里是数字静音（约 −175 dB）。交付页上切轨无效、播放中切轨要等暂停才生效，见常见坑第 30、31 条 |
| 镜头光斑、剪辑页特效模板、Fusion 页宏、播放帧率、Fairlight 自动化、变速曲线（manual） | 没有可用的 API。在时间线上打标记，写进手动收尾清单 | 手动 | — |

### 音乐压低为什么不用 sidechaincompress

最初的计划是用 ffmpeg sidechaincompress，拿人声做侧链压音乐。实际做的时候改成了 `duck.py` 里的增益包络：

- 在 cut_plan 标出的人声段内，增益降到 −duck_db。
- 降下去和恢复都用线性过渡：attack 0.15 秒，release 0.4 秒。
- 两段人声间隔小于 hold（0.6 秒）时并成一段，避免音乐在两句话之间忽高忽低。

改的原因有两个：
1. 包络压多少就是多少，可以测出来核对，测试就是这样卡 −12 dB ±0.5 的。压缩器的实际压低幅度随人声响度变化，写进风格包的 `duck_db` 没法对上。
2. 人声段已经在 cut_plan 里了，不需要再渲染一条人声侧链。

MCP 自带的 mix_plan 不用，原因有两个：它输出的是人声加音乐的整轨混音；它依赖的 numpy 在 MCP 的 venv 里没装。

## 自动建工程

建工程、切工程都由脚本完成，用户不用碰 Resolve 界面。本机实测全程没有弹窗：

1. 先 `project_manager save` 保存用户当前的工程。
2. advanced server `drp create_empty_project {outputPath, timelineName}` 生成一个空 `.drp`（1920×1080、24 fps 的模板，带一条空时间线）。
3. `project_manager safe_project_import {path, name, require_temp_path:false, allow_non_mcp_name:true}`，先 dry_run。约 1.2 秒，不会切走当前工程。
4. `project_manager load {name}`。当前工程已保存时不弹窗。
5. 马上 `save` 一次。还没保存过的新工程上写工程预设可能把工程毁掉（MCP 的 api_truth 有记录），所以先保存再改设置。
6. 删掉模板自带的空时间线（`delete_timelines` 要两步 confirm_token）。
7. `project_settings set_setting timelineFrameRate "29.97"`（按素材定）。只有工程里一条时间线都没有时才写得进去。
8. 再 `save`。

播放帧率（timelinePlaybackFrameRate）和 videoMonitorFormat 写不进去，停在模板的 24，要手动改，写进手动收尾清单。分辨率设置本机没测过，先读回看一眼。

## 探针清单

#1–#12 于 2026-11-02、#13–#17 于 2026-11-07、#18–#22 于 2026-11-14 在本机跑完，结论在上面的表、常见坑和 `learnings.md`。Resolve 或 MCP 升级后重跑。在一次性工程 `vlog-style-QA-*` 里只用合成素材（比如 `test_synthetic.py` 或 ffmpeg testsrc 生成的片子），工程按上一节自动建。每一项都要有结论：

1. **连通性。** 在偏好设置 System > General 里，「External scripting using」要设为 Local。MCP 要能列出工程。Resolve 没开时 MCP 会自己启动它。
2. **帧号。** 读回时间线起始帧，29.97 应为 108000，24 应为 86400。核对 `GetEnd` 是否不含该帧，也就是 end − start = duration。
3. **append。** 用 clipInfos 一次放好 3 段，核对读回的入出点逐帧一致。
4. **转场。** 逐个试 Cross Dissolve、Dip To Color、Smooth Cut，以及 fusion 类的 Brightness Flash、Pan Left、Crash Zoom、Camera Shake。确定 category 和名字该怎么传，素材余量不够时会怎样。
5. **静态推镜。** `set_transform` 设 ZoomX/ZoomY。
6. **Fusion 推拉。** 用 `import_comp` 导入一个带 Transform 关键帧的 .comp，导出首帧、中帧、尾帧三张静帧核对缩放比例，再渲染一次。查 TimelineItem 上有没有 `AddKeyframe`（本机没有）。
7. **标题。** 用单独时间线做标题，放到 V3，导出静帧看文字有没有渲染出来；标题时长能否拉过 5 秒；两种字体能否用 API 写。
8. **字幕。** 字幕预设在 Mac 上能否用，导出时烧录是否生效。静帧里不显示字幕，要渲染一小段看。
9. **DRX。** 带 LUT + CDL + 颗粒的 DRX，以及带 Camera Shake 的 DRX；套用后导出静帧，用 `compare.py look` 对照。
10. **变速。** `set_speed` 设为 50、0、−100，`set_retime` 设为 optical_flow；advanced server 生成的 `.drt` 能否导入、导入后速度对不对。
11. **合成模式。** 滤色要传的数值，导出静帧和混合公式比对。
12. **音频。** AudioVolume 和音频淡入淡出能否写入、能否读回，渲染一条只有音频的文件测电平。
13. **只放音频。** `media_type 2` 把声音单独放到 A1，源区间比画面提前 2 秒，渲染核对声画偏移。同时试 59.94 素材放 29.97 时间线、非零起始时间码（`ffmpeg -timecode`）、单声道素材放立体声轨，以及 `set_voice_isolation_state` 在渲染里是否生效。
14. **静图。** 导入 jpg、png、heic，按帧放短于和长于 5 秒的两段，`set_transform` 缩小放在黑底上；再和 ffmpeg 做成的照片短片比长度、缩放和颜色。
15. **.srt 落位。** 第一条字幕写在第 1 秒，第 300 秒再放一条（29.97 下真实时间和非丢帧时间码在这里差约 9 帧），烧录渲染一小段核对。在带 V1、V2、A1 的小时间线上 `set_subtitle_preset` 前后各读回一次，看条目、音量、缩放、调色有没有丢；再调字号和位置，用 `vision_ocr` 量框高和中心。
16. **按章 CDL 校准。** 测试卡和时间线一样大（灰阶渐变、几档明度和饱和度的色相带、柔和的彩色纹理），做成 bt709 的 ProRes 短片，在一条时间线上放多段。一段不调色，其余各套一个 `safe_set_cdl`：slope 1.2、offset +0.03、offset −0.03、power 0.85、sat 1.25，再一段四项分通道一起（slope 1.15/1/0.9、offset 0.02/0/−0.01、power 0.9/1/1.1、sat 1.2，顺带核对通道顺序）。每段中间导出一张静帧，跑 `grade.py preview --grade <只含这些 CDL 的 grade.json> --chapter <段> <未调色> --graded <已调色>`，看每通道平均 |diff| 和各键 look 差，2/255 以内算通过；再拿 Rec.601、等权亮度系数和先 power 的公式算一遍，确认测试卡分得出对错。LUT 用逐通道的测试 LUT（每个输出通道只看自己的输入，三线性和四面体插值结果一样，能精确预测），分三段：只套 LUT；LUT 和 CDL 同在节点 1；中性两节点 DRX，LUT 在节点 1、CDL 在节点 2。最后给一段已写 CDL 的条目再套一次 DRX，看 CDL 还在不在
17. **电平和成片核对。** 合成素材（testsrc 画面、`say` 念的 6 句中文、前两句轻 8 dB、和弦加底鼓的音乐），手写约 25 秒的 cut_plan：两段对白、照片、带 dip_to_black 的两段 B-roll、V2 插入镜头、片尾 B-roll。跑 subs、duck、`mix.py dialogue`，按 apply.md §7 搭好，渲染只有音频的整片跑 `mix.py master`，写回再渲染一次，看 `master_db` 是否收敛、整片是否落在 −14 ±0.5 LUFS、真峰值 ≤ −1 dBTP。再烧录导出 H.265 Main10，跑 §9 的五条命令，`compare.py final` 要全过；然后用 ffmpeg 做三个坏版本（音频晚 0.4 s、整体 −6 dB、剪掉最后 1 秒），在 Resolve 里再渲一个不烧字幕的，各自只该挂对应的那一项（同步、响度、规格、字幕）
18. **加音轨、改轨名。** 只有 1 条音轨的时间线上加 6 条立体声轨到 A7，读回轨数和 sub_type；A5–A7 写中文轨名（「候选1 测试曲甲 120BPM 免署名」这样），读回要一字不差。每次 `add_track` 都会自动归档时间线，看 `begin_run` 能不能把它们并成一次。
19. **静音一条候选。** 三首合成曲是频率各不相同的正弦（300 Hz −20、1 kHz −14、3 kHz −26 dB RMS），每条单独一轨，一次只出音频的渲染里就能按频率分别量出每条的电平。分别试 `set_track_enable(false)` 和 `set_clip_enabled(false)`，只认渲染：关掉的那条要是静音，开着的那条电平不变。顺便试交付页和剪辑页是否一样。
20. **候选的位置和电平。** clip_infos 往空轨上放，有一条 record_frame 不从 0 开始、源也不从 0 开始；`clip_where` 读回位置。三条 `AudioVolume` 设 0、−6、+6 dB，让渲染里都落到 −20 dB；第一条加 `set_fades` 30/60 帧。
21. **播放头和标记。** `set_current_timecode` 跳到 01:00:20:00 并读回；在第 0 帧写一个带中文名、说明和 custom_data 的标记，在候选 2 的进入点写一个带时长的标记，`get_all` 读回。查 MCP 有没有开始播放的接口。
22. **播放中切轨。** 至少 10 分钟长的时间线（40 秒的在一问一答之间就播完了，循环播放也没生效），A1 300 Hz 开、A2 1 kHz 关。请用户按空格播放，播放中开 A2、关 A1，问用户听到了什么；再请用户暂停，读回轨道状态；再播一次，问用户听到了什么。

结论写回上面的表，并在 `learnings.md` 记一条，写明日期和本机版本。

## 常见坑

第 1–11 条来自 jablonkai/skills 的 davinci-resolve gotchas，在 21.1.0 上实测，本机 21.1.1.10 探针复核过其中能测的部分，第 4、6、8 条按本机结果改过。第 12–14 条是我们自己的约定。第 15 条起是本机探针新发现的。

1. **失败不报错。** 调用失败时只返回 `False`、`None`、空列表或空字符串，不会抛异常，所以每个改动类调用都要检查返回值。`SetProperties` 只要有一个键不认识就整体不生效；`SetSettings` 会逐键生效，前面已经写进去的键不会回滚。
2. **两种帧号。** 下面这些用的是绝对帧号，时间线从 01:00:00:00 开始：
   - append 的 `recordFrame`（MCP 的 `record_frame` 默认相对起点，它替你加上）
   - `GetStart` / `GetEnd`
   - `Timeline.GetStartFrame`

   而 `Timeline.AddMarker` 用的是相对时间线起点的偏移，clipInfos 的 `startFrame` / `endFrame` 用的是源素材帧号，从 0 开始，和素材的起始时间码无关（#13 用起始时间码 04:18:37:25 的素材实测）。cut_plan 里的 `rec_in` 从 0 开始，搭建时要加上读回的起始帧。
3. **append。**
   - 只作用于当前时间线。
   - `trackIndex` 超出现有轨道数时不会报错，片段会落到别的轨道上，所以要先建好轨道。
   - 没传 `mediaType` 时视频和音频一起放，返回值里只有视频条目。
   - 已经在时间线上的条目无法移动或修剪，要改就重建一个时间线版本。
4. **Fusion。** 本机 21.1.1.10 上，`AddFusionComp` 建的 comp 用实时 `fusion_comp` 加 Transform、连线、打 Size 关键帧，渲染是生效的；之前「AddFusionComp 的 comp 永远不会被渲染」的说法，多半是下面的 Lock 问题。推荐路线仍是 `import_comp` 导入最小 `.comp` 模板，一次写好，不依赖实时改节点。
   - 不要在 comp 的 `Lock()` 期间写参数值：读回是对的，但渲染里没有。加节点、连线不受影响。
   - comp 第 0 帧就是条目的第一帧，末尾关键帧要放在「条目时长 − 1」。
   - MediaIn 会自动绑定到所挂条目的素材，模板里不写素材路径，同一份模板能挂到任何条目上。
   - TimelineItem 上没有关键帧 API，动画只能放在 Fusion 里。
   - 中文界面下读回的名字是本地化的（时间线、Transform1大小、交叉叠化），按名字匹配时注意。
5. **Insert\* 会波纹整条时间线。** `InsertFusionTitleIntoTimeline` 等调用会在播放头处做插入编辑，后面所有片段都被推后。所以标题一律先在空的一次性时间线里做好，再当片段放上去。
6. **帧率。** 帧率要在第一条时间线之前设好：`timelineFrameRate` 只有工程里一条时间线都没有时才写得进去，所以自动建工程时要先删掉模板自带的时间线。播放帧率写不进去，只能手动。advanced server 生成的 `.drp` 模板是 1920×1080、24 fps。
7. **渲染设置。**
   - `LoadRenderPreset` 会覆盖格式和编码，所以先加载预设，再设格式和编码，最后设渲染参数。
   - 时间线名里有 `/` 或 `:` 会导致渲染失败，要传 `CustomName`。
   - 写渲染设置之前先保存工程。
   - 本机 21.1.1.10 上 `render get_settings` 报错（「GetRenderSettings requires DaVinci Resolve unknown+」），不要靠它读回，直接 `verify_output` 查输出文件。
   - `render prepare_render_job` 默认要求 `target_dir` 在系统临时目录下，渲染到项目的 `qa/` 要传 `require_temp_target:false`。
   - 只出音频：`load_preset "Audio Only"`，再 `set_settings {ExportVideo:false, ExportAudio:true, AudioFormat:"wav", AudioCodec:"lpcm", SelectAllFrames:true, TargetDir, CustomName}`。编码名用 `get_audio_codecs {format:"wav"}` 查（Linear PCM 是 `lpcm`）。加载这个预设后 `get_format_and_codec` 读回是空的，正常。
   - 发布用 H.265（#17）：`load_preset "H.265 Master"`，`set_format_and_codec {format:"mp4", codec:"H265"}`，再 `set_settings {ExportVideo:true, ExportAudio:true, SelectAllFrames:true, FormatWidth, FormatHeight, FrameRate, EncodingProfile:"Main10", AudioCodec:"aac", AudioSampleRate:48000, ExportSubtitle:true, SubtitleFormat:"BurnIn", TargetDir, CustomName}`。出来是 hevc Main 10、yuv420p10le，帧数和时间线一样。AAC 那一路比画面长 0.078 s（编码器补的尾巴），容器时长跟着音频走，所以量成片时长要看视频流（`compare.py final` 是这样做的）。响度编码前后都是 −14.1 LUFS、真峰值 −1.0 dBTP。
8. **共享当前状态。** 当前工程、当前时间线、当前文件夹、播放头这些状态都是和用户的界面共用的。建工程、切工程由脚本自动完成（见「自动建工程」），但切走之前必须先保存当前工程，也不在用户正在剪的工程上试验。
9. **弹窗会卡住。** Resolve 里有菜单或对话框开着时，脚本调用会一直卡住，不报错。遇到这种情况就请用户去关掉，不要循环重试。
10. **AI 类调用是异步的。** 语音转字幕、转写、场景检测这类调用会立即返回 True，要轮询对应的 `Get…Status()`，等到 Complete 再读结果。MCP 里 `background:true` 的调用（比如 `import_timeline_checked`）返回 job_id，用 `resolve_control job_status` 轮询。
11. **多个脚本会互相干扰。** 两个脚本同时驱动同一个 Resolve，会在共享状态上交错执行。一次只跑一个任务。
12. **只读回不算通过。** 读回只能说明条目放对了位置，调色、标题、Fusion 有没有真的渲染出来，要导出静帧看。
13. **绝对路径。** 导入素材时由 `paths.py resolve` 现拼绝对路径，不写进任何文件。迁移到 NAS 后，用 `MediaPool.RelinkClips` 把媒体池指回新的 root。
14. **不要在仓库里运行 MCP 的 `install.py`。** 它会写出 `.mcp.json`，有可能被一起提交。
15. **自动归档会改当前文件夹。** MCP 的破坏性操作（改速度、改 transform、套预设等）会先把时间线归档到 Archive 文件夹，当前文件夹可能停在 Archive 上，之后导入的素材也落进去。每次 append 或导入之前先 `media_pool set_current_folder {path:"Master"}`。
16. **两步确认。** `delete_timelines`、`safe_apply_drx` 第一次调用只返回 confirm_token，带上它再调一次才执行。
17. **转场。**
   - 传短名就行（Cross Dissolve、Dip To Color Dissolve、Smooth Cut、Brightness Flash、Pan Left、Crash Zoom、Camera Shake），fusion 类要带 category。
   - 素材余量不够，或者名字不认识，都只返回 `success:false`，不给原因。
   - 读回的名字是中文：交叉叠化、浸入颜色叠化、平滑剪接。
18. **静帧核对的做法。** `timeline_markers set_current_timecode` 定位，`project_settings export_frame_as_still {path}` 导出。`timeline_frame capture {frame}` 也行，但图会直接进上下文。
   - 用 ffmpeg 解合成素材或无色彩标签的素材做比对时，要指定 bt709 矩阵；默认按 bt601 解，颜色对不上。
   - 静帧里不显示字幕，字幕要渲染一小段看。
19. **调色 DRX 的坑。** `drx lut_apply` 会把一级调色节点丢掉；`drx merge` 会把底图里的节点有损重写，所以底图只用 `drx generate` 出的中性图，真实节点全放 newNodes；LUT 最后用 `graph set_lut` 单独套。
20. **混帧率素材的源出点。** `project_manager snapshot` 给的 `source_end` 对 59.94 素材差一帧（299 对 300），以 `timeline_item get_source_end_frame` 为准。`timeline clip_where` 也一样：59.94 素材请求 600–1200 读回 `source_end` 1199，29.97 素材请求 300–600 读回 600。读回核对源出点时留 1 个源帧的容差，`compare.py readback` 已经这样做（`compare.py normalize` 把 clip_where 的回复整理成它的输入）。
21. **.srt 导入会让 Resolve 崩溃。** 用 clip_infos（带位置参数）append .srt，Resolve 直接崩溃。要先用只传 `clip_ids` 的旧式 append 把 .srt 放到一条空时间线上，字幕按 .srt 里的时间落位（从时间线起点算的秒数 × 时间线帧率，取整，#15），再放视频。崩溃后重开 Resolve、`project_manager load` 不弹窗，但没保存的改动会丢，所以每一步做完都 `save`。
22. **音量的键名。** 21.1 的条目属性是 `AudioVolume`（dB）、`AudioPan` 等。MCP 的 `set_audio Volume` 写的是旧键，返回 `Volume:false` 加一条 known_limitation，实际什么都没改；用 `set_property {key:"AudioVolume"}`。音频条目上不带 key 的 `get_property` 返回 false，要逐个键读。
23. **合成模式要传数字。** 取 Resolve 的 `COMPOSITE_*` 常量：NORMAL 0、ADD 1、MULTIPLY 4、SCREEN 5、OVERLAY 6、SOFTLIGHT 8、LIGHTEN 10、LINEAR_DODGE 19、INVERTED_LUM 31。`set_composite` 返回的 `CompositeMode:true` 不是读回值，要再 `get_composite`。
24. **标题时间线的媒体池条目（2026-11-07 起过时）。** 之前以为 MCP 不给时间线的媒体池条目 id，要用只读脚本去找。实测 `folder get_clips` 会列出时间线条目的名字和 id，`media_pool_item get_timeline {clip_id}` 能确认它是哪条时间线（`is_timeline:true`），直接用这两个调用，不用另写脚本。
25. **advanced server 的坑。**
   - `fusion generate` 写出的关键帧格式不对；`.comp` 用手写的最小模板。
   - `drx parse` 会把 server 弄崩。
   - server 断开时，可以用 node 直接 import 它的 tool 模块调 handler，参数和 MCP 调用一样。
26. **MCP 的时间码换算。** MCP 按名义 30 fps、从 0 帧起算把帧号换成时间码。正常的 108000 起点时间线没问题；起始帧和显示时间码对不上的时间线（比如离线 `.drt` 导入的那条），按 MCP 的时间码 seek 会落到片段外，导出全黑静帧。
27. **原生静图不认 `start_frame`/`end_frame`。** jpg、png、heic 都能导入（读回 FPS 24、Frames 1），但 append 时不管传多长，都放成 150 帧，也就是偏好设置里默认的静图时长 5 秒。不改偏好设置，照片一律先做成短片（见路线表「照片」）。
28. **字幕预设。**
   - 不给 `preset` 直接拒绝；没缓存的预设不能 dry run，只能在一次性工程里真跑。
   - 它导出 DRT 再导入，生成一个新时间线（名字取 `revision_name`），原时间线留着。之后放标题、调色、读回都要先 `timeline set_current` 到新的那条。#15 前后读回：V1、V2、A1、字幕的位置和源帧、AudioVolume、缩放都没变，渲染里 CDL 还在。markers、V3 标题等其他 Fusion 合成没验证，所以搭建时先套字幕预设再放标题，或者套完再读回一次。
   - 可编辑项只有 font、fontStyle、size、position、文字颜色和 textFillMode，没有描边。要描边就换带描边的预设（未探针），或者写进手动收尾清单。
   - 风格包里的字高是 `vision_ocr` 的框高，不是笔画高度，量的时候要用同一种方法。PingFang SC Regular 实测：`size 0.040` + `position [0.5, 0.032]` 框高 4.44%、中心 y 0.9555；`size 0.037` + `position [0.5, 0.023]` 框高 4.03%、中心 y 0.965；`size 0.053` 框高 6.1%。估算用「框高 ≈ 1.1 × size」「中心 y ≈ 0.9875 − position y」，套完渲一小段量一次。
   - 框高跟字有关（#17，同一套 `size 0.040` + `position [0.5, 0.032]`）：没有标点的句子框高 4.72%、中心 y 0.956；带逗号的 5.21–5.60%、中心 0.958，因为逗号伸到字底下。参考片的字幕不带标点，所以核对字号只看不带逗号、顿号、括号和 g/j/p/q/y 的句子（`compare.py` 的 `LOW_INK`）。
29. **CDL 和 LUT 的先后。** 同一个节点里 Resolve 先算 CDL 再算 LUT（#16）。所以风格包带 LUT 时，LUT 放节点 1，CDL 放节点 2（`grade.py solve --node 2`），这样才是 `--stills` 假设的「先 LUT 后 CDL」。`safe_apply_drx` 会替换整个节点图，已经写进去的 CDL 一起没了（#16 实测静帧和未调色逐像素相同），所以要先套 DRX、再写 CDL；重套 DRX 之后 CDL 要重写。
30. **交付页上切轨无效。** Resolve 停在交付页时，`set_track_enable` 对音轨和视频轨都返回 True，但什么都没改：读回还是 true，渲染里三条候选照样都在（#19）。渲染会把 Resolve 留在交付页，所以渲染之后要切轨，先 `resolve_control get_page`，不是 `edit` 就 `open_page edit`（只在没在播放时切页，见下一条），再切轨并读回。剪辑页上切轨读回和渲染都对。
31. **播放中改时间线的调用会被挂起。** 用户在播放时，`set_track_enable` 不生效，调用一直不返回，读播放头的 `get_current_timecode` 也一样（#22：两条切轨各卡了 120 秒以上，用户一直听到旧的那条）。用户一暂停，挂起的调用马上执行完，状态也对；再按空格播放的就是新的那条。`get_page` 在播放中照样立刻返回。所以：
   - 试听时请用户先暂停，再说「换 k」；agent 切轨、读回，用户再按空格。播放中说的也不会丢，暂停时才生效。
   - 一个调用还没返回时不要接着发别的 Resolve 调用（第 11 条）。
   - MCP 的 playback_guard 靠「隔 0.18 秒读两次播放头，变了就是在播放」，本机读播放头会被挂起而不是返回在动的播放头，所以这个检测在 21.1.1.10 上不起作用；更不能在播放中切页，19.1 上播放中切页会把 Resolve 卡死（MCP 源码注释）。
32. **没有播放接口。** 脚本 API 不能开始、停止播放，也不能查是否在播放（MCP 4.15.3 源码里没有，playback_guard 的注释也这么写）。播放只能请用户按空格；agent 能做的是移播放头（停着时 `set_current_timecode` 立刻生效）和打标记。

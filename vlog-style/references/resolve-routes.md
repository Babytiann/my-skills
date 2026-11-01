# Resolve 实施路线

本机环境：DaVinci Resolve Studio 21.1.1.10，加上 samuelgursky/davinci-resolve-mcp v4.15.3。它注册成两个 server：

- `davinci-resolve` 是实时 server，直接驱动正在运行的 Resolve。
- `davinci-resolve-advanced` 是离线 server，生成 .drt 和 .drx 文件，不需要打开 Resolve。

下文的工具名取自 MCP 的文档和源码，实际调用时以 MCP 工具列表为准。

「状态」一列的含义：
- **已验证**：MCP 贡献者在 21.1.0.x 上实际渲染核对过。
- **待探针**：本机还没跑过。
- **手动**：没有 API，只能手动做。

本机跑完探针（见下一节）后，把结果写进「本机」一列。用不了的项改走备选路线，`style.json` 里 `resolve[].status` 也一起改。

## 路线表

| 风格元素（route） | 做法 | 状态 | 本机 |
|---|---|---|---|
| 剪辑点、B-roll、J/L cut、音乐床（live_api） | `append_to_timeline`，传 clipInfos 一次放好。`startFrame`/`endFrame` 是源素材帧号，从 0 开始，end 不含该帧。每次 append 之后都读回 | 已验证 | 待探针 |
| 叠化（live_api） | `add_transition` Cross Dissolve，category 为 `simple`。两侧素材都要有余量。从时间线末尾往前加，以免条目序号漂移 | 已验证 | 待探针 |
| 黑场过渡、Smooth Cut（live_api） | `add_transition`：Dip To Color Dissolve / Smooth Cut | 待探针 | 待探针 |
| 闪白、甩镜、急推、抖动转场（fusion） | `add_transition`，category 设为 `fusion`：Brightness Flash、Pan Left/Right、Crash Zoom、Camera Shake。要传短名还是完整 ID，由探针确定 | 待探针 | 待探针 |
| 恒定慢动作（live_api） | `set_speed`。60p 素材放在 30p 或 24p 时间线上，50% 或 40% 的慢动作最干净。60p 时间线还要 `set_retime` 设为 `optical_flow` | 已验证 | 待探针 |
| 定格、倒放（live_api） | `set_speed` 设为 0 或负值；倒放也可以走离线 .drt | 待探针 | 待探针 |
| 变速曲线（offline_drt） | 由 advanced server 生成整条时间线的 `.drt`（只能是线性分段），再用 `import_timeline_checked` 导入。变速段的声音不跟着变速，要另外处理 | 只在 19.1 上验证过渲染，21 上只验证过读回 | 待探针 |
| 静态推镜（live_api） | `set_transform` 设置 ZoomX/ZoomY。4K 素材放在 4K 时间线上，推镜就是放大，会损失清晰度 | 待探针 | 待探针 |
| 动态推拉、Ken Burns（fusion） | 生成 `.comp`（MediaIn → Transform → MediaOut，在 Size/Center 上打关键帧），用 ImportFusionComp 挂到条目上。**不要用 AddFusionComp**，原因见常见坑第 4 条；也不要用 Dynamic Zoom，它的属性名在 API 定义和 MCP 里对不上 | 19.1 上验证过渲染 | 待探针 |
| 持续抖动（offline_drx） | 在 look DRX 里加 ResolveFX Camera Shake，MotionScale 参数已校准 | 待探针 | 待探针 |
| 遮幅（live_api） | `set_output_blanking` | 已验证 | 待探针 |
| 地点标题、章节卡（live_api） | 每条文字单独建一个时间线，在里面 `insert_fusion_title` + `set_title_text`，再把这个时间线当片段放到 V3。同一个标题时间线放多次时文字是共用的，所以每条不同的文字各建一个。标题之间不能重叠 | 只在 Windows 上验证过 | 待探针 |
| 字幕（live_api） | 用 Resolve 自带的语音转字幕，或者导入 .srt，再 `set_subtitle_preset`。导出时的烧录设置在 21.1 上没有生效的证据；不行就把字幕做成 Fusion 标题 | 预设只在 Windows 上验证过；烧录未验证 | 待探针 |
| 调色（offline_drx） | 生成一个 look DRX（LUT + CDL + 颗粒），每个片段套用一次。套用 DRX 会替换整个节点图，所以只能套一次。风格里没有颗粒时，可以只用 `set_lut` + SetCDL。SetCDL 写进去读不回来，只能导出静帧核对。颗粒可调的参数：GrainStrength、GrainMean、GrainSkew、Opacity | LUT 已验证；DRX 和颗粒待探针 | 待探针 |
| 光晕、漏光（fusion） | 光晕用 Fusion Glow。漏光素材由用户提供，叠在 V2，合成模式设为滤色；这里要传数字，滤色对应哪个数待探针 | 待探针 | 待探针 |
| 音乐压低（offline_ffmpeg） | 用 `duck.py` 渲染一条只有音乐的压低版音乐床，放在 A2；原始音乐放在一条静音的 A3 上，方便手动重混 | 离线可用（测试实测 −12 dB ±0.5） | 不涉及 Resolve |
| 淡入淡出（live_api） | `set_fades`。片尾淡出也用它，不用转场：最后一个片段后面没有片段，单边转场会出错 | 视频已验证，音频待探针 | 待探针 |
| 镜头光斑、剪辑页特效模板、Fusion 页宏、播放帧率、Fairlight 自动化（manual） | 没有 API。在时间线上打标记，写进手动收尾清单 | 手动 | — |

### 音乐压低为什么不用 sidechaincompress

最初的计划是用 ffmpeg sidechaincompress，拿人声做侧链压音乐。实际做的时候改成了 `duck.py` 里的增益包络：

- 在 cut_plan 标出的人声段内，增益降到 −duck_db。
- 降下去和恢复都用线性过渡：attack 0.15 秒，release 0.4 秒。
- 两段人声间隔小于 hold（0.6 秒）时并成一段，避免音乐在两句话之间忽高忽低。

改的原因有两个：
1. 包络压多少就是多少，可以测出来核对，测试就是这样卡 −12 dB ±0.5 的。压缩器的实际压低幅度随人声响度变化，写进风格包的 `duck_db` 没法对上。
2. 人声段已经在 cut_plan 里了，不需要再渲染一条人声侧链。

MCP 自带的 mix_plan 不用，原因有两个：它输出的是人声加音乐的整轨混音；它依赖的 numpy 在 MCP 的 venv 里没装。

## 探针清单

探针只在用户同意后跑。用户需要先打开 Resolve。在一次性工程 `vlog-style-QA-probe` 里只用合成素材（比如 `test_synthetic.py` 生成的片子），每一项都要有结论：

1. **连通性。** 在偏好设置 System > General 里，「External scripting using」要设为 Local。MCP 要能列出工程。
2. **帧号。** 读回时间线起始帧，29.97 应为 108000，24 应为 86400。核对 `GetEnd` 是否不含该帧，也就是 end − start = duration。
3. **append。** 用 clipInfos 一次放好 3 段，核对读回的入出点逐帧一致。
4. **转场。** 逐个试 Cross Dissolve、Dip To Color、Smooth Cut，以及 fusion 类的 Brightness Flash、Pan Left、Crash Zoom、Camera Shake。确定 category 和名字该怎么传，素材余量不够时会怎样。
5. **静态推镜。** `set_transform` 设 ZoomX/ZoomY。
6. **Fusion 推拉。** 用 ImportFusionComp 导入一个带 Transform 关键帧的 .comp，导出首帧、中帧、尾帧三张静帧核对缩放比例。查 TimelineItem 上有没有 `AddKeyframe`（用 dir 查）。
7. **标题。** 用单独时间线做标题，放到 V3，导出静帧看文字有没有渲染出来；标题时长能否改，默认 5 秒的 Fusion 标题能否拉长。
8. **字幕。** 字幕预设在 Mac 上能否用，导出时烧录是否生效。
9. **DRX。** 带 LUT + CDL + 颗粒的 DRX，以及带 Camera Shake 的 DRX；套用后导出静帧，用 `compare.py look` 对照。
10. **变速。** `set_speed` 设为 0.5、0、−1，`set_retime` 设为 optical_flow；advanced server 生成的 `.drt` 在 21.1.1 上能否导入，导入后能否渲染出变速。
11. **合成模式。** 滤色要传的数值。
12. **音频。** AudioVolume 和音频淡入淡出能否写入、能否读回。

结论写回上面的表，并在 `learnings.md` 记一条，写明日期和本机版本。

## 常见坑

第 1–11 条来自 jablonkai/skills 的 davinci-resolve gotchas，在 21.1.0 上实测；本机 21.1.1 要靠探针复核。第 12–14 条是我们自己的约定。

1. **失败不报错。** 调用失败时只返回 `False`、`None`、空列表或空字符串，不会抛异常，所以每个改动类调用都要检查返回值。`SetProperties` 只要有一个键不认识就整体不生效；`SetSettings` 会逐键生效，前面已经写进去的键不会回滚。
2. **两种帧号。** 下面这些用的是绝对帧号，时间线从 01:00:00:00 开始：
   - append 的 `recordFrame`
   - `GetStart` / `GetEnd`
   - `Timeline.GetStartFrame`

   而 `Timeline.AddMarker` 用的是相对时间线起点的偏移，clipInfos 的 `startFrame` / `endFrame` 用的是源素材帧号，从 0 开始。cut_plan 里的 `rec_in` 从 0 开始，搭建时要加上读回的起始帧。
3. **append。**
   - 只作用于当前时间线。
   - `trackIndex` 超出现有轨道数时不会报错，片段会落到别的轨道上，所以要先建好轨道。
   - 没传 `mediaType` 时视频和音频一起放，返回值里只有视频条目。
   - 已经在时间线上的条目无法移动或修剪，要改就重建一个时间线版本。
4. **Fusion。** `AddFusionComp` 返回的 comp 永远不会被渲染：节点看着连好了，剪辑页和渲染却都忽略它。要用 `ImportFusionComp` 导入 `.comp` 文件。对已经挂上的 comp（标题自带的 comp，或者导入的 comp）做实时修改，是能渲染出来的。
5. **Insert\* 会波纹整条时间线。** `InsertFusionTitleIntoTimeline` 等调用会在播放头处做插入编辑，后面所有片段都被推后。所以标题一律先在空的一次性时间线里做好，再当片段放上去。
6. **帧率。** 新工程默认 3840×2160。帧率和分辨率要在第一条时间线、第一个片段之前设好，时间线里有了媒体之后帧率就锁定了。
7. **渲染设置。**
   - `LoadRenderPreset` 会覆盖格式和编码，所以先加载预设，再设格式和编码，最后设渲染参数。
   - 时间线名里有 `/` 或 `:` 会导致渲染失败，要传 `CustomName`。
   - 写渲染设置之前先保存工程。
8. **共享当前状态。** 当前工程、当前时间线、当前文件夹、播放头这些状态都是和用户的界面共用的。`LoadProject` 或 `CreateProject` 会把用户正在开的工程切走，必须先问用户，再保存当前工程。
9. **弹窗会卡住。** Resolve 里有菜单或对话框开着时，脚本调用会一直卡住，不报错。遇到这种情况就请用户去关掉，不要循环重试。
10. **AI 类调用是异步的。** 语音转字幕、转写、场景检测这类调用会立即返回 True，要轮询对应的 `Get…Status()`，等到 Complete 再读结果。
11. **多个脚本会互相干扰。** 两个脚本同时驱动同一个 Resolve，会在共享状态上交错执行。一次只跑一个任务。
12. **只读回不算通过。** 读回只能说明条目放对了位置，调色、标题、Fusion 有没有真的渲染出来，要导出静帧看。
13. **绝对路径。** 导入素材时由 `paths.py resolve` 现拼绝对路径，不写进任何文件。迁移到 NAS 后，用 `MediaPool.RelinkClips` 把媒体池指回新的 root。
14. **不要在仓库里运行 MCP 的 `install.py`。** 它会写出 `.mcp.json`，有可能被一起提交。

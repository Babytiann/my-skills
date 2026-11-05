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
- 修法：测量只负责「稳不稳」（`zoom_eff` ≥ 0.95，排除人走过、转身造成的假推拉）；数字还是机位由 Claude 看 `strips/moves_*.jpg` 判，aggregate 只按 digital 算推拉比例

## 2026-11-01 黑场判定只看两侧基线：极夜片里大量误报
- 场景：同上，measure_video.py 的 luma_event / snap_cuts
- 做法：最低亮度 < 0.06 且两侧基线中位数 > 0.12 就算黑场，剪辑点挪到黑场中点
- 问题：13 个黑场候选里 6 个是硬切进暗镜头，剪辑点被挪偏 10–12 帧；黑底字卡两头的硬切被挪到一起，字卡从 23 帧变成 5 帧。看事件条发现
- 修法：黑场、闪白在 0.4 秒窗口内两头都要回到基线才算；snap 每次从 `cuts[].raw` 算起。test_synthetic.py 加了 LumaEventTest

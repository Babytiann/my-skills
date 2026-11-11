# links/polar-night 的参考来源

系列《当太阳不再升起》的两集，都是 Linksphotograph 本人投稿。

| 项 | 第 1 集 | 第 2 集 |
|---|---|---|
| 参考 id | BV1sHqPBLExZ | BV1HrrjB9EUj |
| 链接 | https://www.bilibili.com/video/BV1sHqPBLExZ | https://www.bilibili.com/video/BV1HrrjB9EUj |
| 标题 | 当太阳不再升起，我们的世界会变成什么样？（集标题卡上是「0.9° 的太阳」） | Links｜我来到了一个24小时都是黑夜的地方（片名叠字是「坠入极夜」） |
| UP 主 | Linksphotograph（mid 3816626，https://space.bilibili.com/3816626） | 同左 |
| 发布日期 | 2026-01-07 | 2026-01-14 |
| 时长 | 29:47（页面），下载实测 1786.4 s（29:46）；单个分 P，内嵌字幕 | 30:26（页面），下载实测 1825.4 s（30:25）；单个分 P，内嵌字幕 |
| 测量用的版本 | B 站 1080p 档：H.264 1920×1080、29.97 fps、bt709 SDR；540p 代理（960×540） | 同左 |
| 测量日期 | 2026-11-10（判读 2026-11-11） | 2026-11-10（判读 2026-11-11） |
| 用到的部分 | 全片，切成 12 段（6 章）。赞助口播（15:55.9–16:45.2）和片尾致谢（28:47.4–28:57.3）记成 interlude，只记结构位置，不进景别统计；CG、AI 生成画面、照片、黑底卡和碎片也不进 | 全片，切成 12 段（9 章），没有赞助段。地图、AI 生成画面、彩条和碎片不进景别统计 |

## 怎么测的

- 画面：TransNetV2 + PySceneDetect 找剪辑点（两路一致的第 1 集 266 个、第 2 集 301 个；被否掉的 4 个、2 个）；LK 光流测运动；Lab look card（逐个关键帧遮掉镜头自己的黑边）；Apple Vision OCR（每秒 4 帧）。
- 声音：ebur128 响度；beat_this 拍点，librosa 交叉核对（第 1 集对不上，第 2 集一致）。烧录字幕覆盖了旁白，平台没有字幕，两集都用 `--no-asr`，没有跑 whisper；20 s 以上没有字幕的段落在总览图上都是配乐的画面段落，没有对镜头说话。
- 判读：两集各看 7 张总览图、24 个推拉样本，以及转场候选（20 个、8 个）和抽样硬切（12 条、13 条）前后各 8 帧的对照图，填了各自的 labels.json。推拉看不准的取 10%、90% 两帧比较上下半的缩放；一个镜头里的假剪辑点看帧差。之后两集一起 `aggregate.py draft`，basis = multi_reference。

## 保留了什么

- 仓库里：STYLE.md、style.json、本文件。
- 本地 `<root>/styles/links/polar-night/stills/`：10 张 320×180 关键帧，每集 5 张，只用来做验收对照。
- 参考片本身、代理、抽帧和事件条在 `<root>/cache/refs/BV1sHqPBLExZ/` 和 `<root>/cache/refs/BV1HrrjB9EUj/`。用户审过 STYLE.md 后先 `cleanup.py keep`（两集都要，`--style links`），再移进废纸篓。

## 后续

- 2026-11-01：第一版只用第 1 集做成（当时叫 polar-night）。
- 2026-11-05：风格包搬进博主专辑 `styles/links/`，改名 `links/polar-night`。原始测量在这之前已经随缓存移进废纸篓，没有留在 `<root>/styles/links/refs/`。
- 2026-11-10：第 1 集重新下载、重测；加入第 2 集（同一系列），一起测量。
- 2026-11-11：两集分别判读，重建这个包（旧包的数字作废，改了什么见 STYLE.md 炼化记录）。

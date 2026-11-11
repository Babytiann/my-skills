# links/longyearbyen 的参考来源

| 项 | 内容 |
|---|---|
| 参考 id | BV1H5xie8EJZ |
| 链接 | https://www.bilibili.com/video/BV1H5xie8EJZ |
| 标题 | 我来到了世界上最北的城市，一个永久免签的地方｜Links的北极Vlog 上集 |
| UP 主 | Linksphotograph（mid 3816626，https://space.bilibili.com/3816626） |
| 发布日期 | 2024-09-29 |
| 时长 | 25:21（页面），下载实测 1520.4 s；单个分 P，内嵌字幕。北极 vlog 两集里的上集 |
| 测量用的版本 | B 站 1080p 档：AV1 1920×1080、29.97 fps 恒定帧率、bt709 SDR 8 bit，没有黑边；540p 代理（960×540） |
| 测量日期 | 画面 2026-11-10；文字和声音 2026-11-11 重测（字幕带的选取修过之后，见 STYLE.md 炼化记录） |
| 用到的部分 | 全片。切成 15 段，12 个章节参与章节间一致性比较；没有赞助段。地图 4 张、CG 1 张、画作 1 张、黑底照片 21 张、黑底卡 4 张、录屏 7 段、碎片 6 个和结尾的黑镜头不进景别统计 |

## 怎么测的

- 画面：TransNetV2 + PySceneDetect 找剪辑点（两路一致 251 个，没有被否掉的）；LK 光流测运动；Lab look card（逐个关键帧遮掉镜头自己的黑边）；Apple Vision OCR（每秒 4 帧）。
- 文字和声音：2026-11-10 第一次测时，字幕带落在 y 0.884 的小字上，字幕被分成了标题，人声占比只有 0.026。主会话修了 `stats.classify_text` 的字幕带选取之后，2026-11-11 重测了文字和声音。
- 声音：ebur128 响度；beat_this 拍点，librosa 交叉核对（两者对不上）。烧录字幕覆盖了旁白和对话，平台没有字幕，没有跑 whisper。
- 判读：看 6 张总览图、2 张推拉对照图，以及 36 个事件（25 个转场候选、11 条抽样硬切）前后各 8 帧的对照图，不清楚的再看事件条，填了 labels.json。推拉看不准的取 10%、90% 两帧比较上下半的缩放。

## 保留了什么

- 仓库里：STYLE.md、style.json、本文件。
- 本地 `<root>/styles/links/longyearbyen/stills/`：10 张 320×180 关键帧，只用来做验收对照。
- 参考片本身、代理、抽帧和事件条在 `<root>/cache/refs/BV1H5xie8EJZ/`。用户审过 STYLE.md 后先 `cleanup.py keep BV1H5xie8EJZ --style links`，再移进废纸篓。

## 后续

- 2026-11-11：第二批入池（用户按地点挑：极地），和第一批三条一起并进博主级包 `links`。下集没有测。

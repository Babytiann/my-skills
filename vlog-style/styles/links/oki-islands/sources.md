# links/oki-islands 的参考来源

| 项 | 内容 |
|---|---|
| 参考 id | BV1ydjizBE1o |
| 链接 | https://www.bilibili.com/video/BV1ydjizBE1o |
| 标题 | Links｜隐藏着600万年的地球记忆！探索日本最神秘的离岛群--隐岐诸岛 ｜SONY FX2 |
| UP 主 | Linksphotograph（mid 3816626，https://space.bilibili.com/3816626） |
| 发布日期 | 2025-05-29 |
| 时长 | 24:40（页面），下载实测 1479.5 s；单个分 P，内嵌字幕 |
| 测量用的版本 | B 站 1080p 档：AV1 1920×1080、29.97 fps、bt709 SDR 8 bit；540p 代理（960×540） |
| 测量日期 | 2026-11-10 |
| 用到的部分 | 全片。切成 16 段，11 个章节参与章节间一致性比较；两段讲相机功能的口播（2:38.3–2:58.0、21:28.0–22:06.8）按赞助记 interlude，只记结构位置，不进景别统计；地图动画、5 张照片插入、彩条和 2 个片尾碎片也不进景别统计 |

## 怎么测的

- 画面：TransNetV2 + PySceneDetect 找剪辑点（两路一致 263 个，没有被否掉的）；LK 光流测运动；Lab look card（逐个关键帧遮掉镜头自己的黑边，2026-11-10 改的测量）；Apple Vision OCR（每秒 4 帧）。
- 声音：ebur128 响度；beat_this 拍点，librosa 交叉核对（两者对不上）。烧录字幕覆盖了旁白，平台没有字幕，没有跑 whisper。
- 判读：看 7 张总览图、2 张推拉对照图、6 条转场候选、12 条抽样硬切和 10 条叠字的事件条，填了 labels.json。推拉看不准的取 10%、90% 两帧比较上下半的缩放。

## 保留了什么

- 仓库里：STYLE.md、style.json、本文件。
- 本地 `<root>/styles/links/oki-islands/stills/`：10 张 320×180 关键帧，只用来做验收对照。
- 参考片本身、代理、抽帧和事件条在 `<root>/cache/refs/BV1ydjizBE1o/`。用户审过 STYLE.md 后先 `cleanup.py keep BV1ydjizBE1o --style links`，再移进废纸篓。

## 后续

- 2026-11-10：第二批入池（用户按地点挑：日本），和第一批三条一起并进博主级包 `links`。

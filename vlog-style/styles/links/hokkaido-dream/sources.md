# links/hokkaido-dream 的参考来源

| 项 | 内容 |
|---|---|
| 参考 id | BV1xF4m1L7Q2 |
| 链接 | https://www.bilibili.com/video/BV1xF4m1L7Q2 |
| 标题 | Vlog｜在北海道做了一场不愿醒来的梦｜4K杜比视界 |
| UP 主 | Linksphotograph（mid 3816626，https://space.bilibili.com/3816626） |
| 发布日期 | 2024-03-06 |
| 时长 | 24:30（页面），下载实测 1469.0 s；单个分 P，内嵌字幕 |
| 测量用的版本 | B 站 1080p 档：AV1 1920×960（2:1）、29.97 fps、bt709 SDR 8 bit。标题写杜比视界，下载到的这一档是 SDR；540p 代理（1080×540） |
| 测量日期 | 2026-11-10 |
| 用到的部分 | 全片。切成 12 段，9 个章节参与章节间一致性比较；没有赞助段。24 张照片插入、3 张黑底卡、1 段彩条和 5 个 0.5 s 以下的碎镜头不进景别统计 |

2024 年北海道冬季之旅的下集；上集是《相机取景器中的雪国！vlog｜一年一度北海道的旅行｜907X CFV100C》（BV1i6421M7jg，没有炼）。

## 怎么测的

- 画面：TransNetV2 + PySceneDetect 找剪辑点（两路一致 252 个，没有被否掉的）；LK 光流测运动；Lab look card；Apple Vision OCR（每秒 4 帧）。
- 声音：ebur128 响度；beat_this 拍点，librosa 交叉核对。烧录字幕覆盖了旁白，平台没有字幕，没有跑 whisper。
- 判读：看 6 张总览图、2 张推拉对照图、19 条转场候选、11 条抽样硬切和 9 条叠字的事件条，填了 labels.json。推拉看不准的取 10%、90% 两帧比较上下半的缩放。

## 保留了什么

- 仓库里：STYLE.md、style.json、本文件。
- 本地 `<root>/styles/links/hokkaido-dream/stills/`：10 张 320×160 关键帧，只用来做验收对照。
- 参考片本身、代理、抽帧和事件条在 `<root>/cache/refs/BV1xF4m1L7Q2/`。用户审过 STYLE.md 后先 `cleanup.py keep BV1xF4m1L7Q2 --style links`，再移进废纸篓。

## 后续

- 2026-11-10：第二批入池（用户按地点挑：日本），和第一批三条一起并进博主级包 `links`。

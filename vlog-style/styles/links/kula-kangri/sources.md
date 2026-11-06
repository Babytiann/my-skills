# links/kula-kangri 的参考来源

| 项 | 内容 |
|---|---|
| 参考 id | BV1jy2CBNEDL |
| 链接 | https://www.bilibili.com/video/BV1jy2CBNEDL |
| 标题 | 和李现，徒步在西藏库拉岗日 |
| UP 主 | Linksphotograph（mid 3816626，https://space.bilibili.com/3816626） |
| 发布日期 | 2025-12-10 |
| 时长 | 37:33，单个分 P，内嵌字幕 |
| 测量用的版本 | 1080p 29.97 fps 下载，540p 代理 |
| 测量日期 | 2026-11-05 |
| 用到的部分 | 全片。13 个章节参与章节间一致性比较；没有赞助段 |

## 怎么测的

- 画面：TransNetV2 + PySceneDetect 找剪辑点（代理的时间戳跳帧已映射到恒定帧率时钟）；LK 光流测运动；Lab look card；Apple Vision OCR（每秒 4 帧）。
- 声音：ebur128 响度；beat_this 拍点。烧录字幕已经覆盖对话，平台没有字幕，没有跑 whisper。
- 判读：Claude 看 11 张总览图、推拉对照图、20 条转场候选、20 条抽样硬切、72 个标题的总览和事件条，以及 1080p 标题裁切，填了 labels.json。

## 保留了什么

- 仓库里：STYLE.md、style.json、本文件。
- 本地 `<root>/styles/links/kula-kangri/stills/`：10 张 320 宽关键帧，只用来做验收对照。
- 本地 `<root>/styles/links/refs/BV1jy2CBNEDL/`：measure_video.json、measure_audio.json、labels.json（已去掉转写和字幕原文），用来合并博主级包。
- 参考片本身、代理、抽帧和事件条：2026-11-06 已移进废纸篓；以后要重测，按上面的链接重新下载。

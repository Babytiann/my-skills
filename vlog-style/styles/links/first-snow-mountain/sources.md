# links/first-snow-mountain 的参考来源

| 项 | 内容 |
|---|---|
| 参考 id | BV1SSQLBREVV |
| 链接 | https://www.bilibili.com/video/BV1SSQLBREVV |
| 标题 | 和李现，攀登人生第一座雪山 |
| UP 主 | Linksphotograph（mid 3816626，https://space.bilibili.com/3816626） |
| 发布日期 | 2026-04-16 |
| 时长 | 页面 55:12，下载实测 3311 s；单个分 P，内嵌字幕，没有 B 站 AI 字幕 |
| 测量用的版本 | 1080p 29.97 fps 下载，540p 代理 |
| 测量日期 | 2026-11-06 |
| 用到的部分 | 全片。14 个章节参与章节间一致性比较；没有赞助段 |

## 怎么测的

- 画面：TransNetV2 + PySceneDetect 找剪辑点（代理的时间戳跳帧已映射到恒定帧率时钟）；LK 光流测运动；Lab look card；Apple Vision OCR（每秒 4 帧）。
- 声音：ebur128 响度；beat_this 拍点。烧录字幕已经覆盖对话，没有跑 whisper。
- 配乐（2026-11-14 补测）：`measure_music.py`（demucs + CLAP）。原片早已清掉，按出处重下。B 站这条的音频源流第 112–127 片（560–640 s）是坏的，66k 和 192k 两档坏在同一处，重下也一样；跳过坏片后这 80 s 按静音测，配乐覆盖率至多偏低 2.4 个百分点。见 learnings.md 2026-11-14。
- 判读：Claude 看总览图、推拉对照图（24 个镜头）、37 条转场候选、24 条抽样硬切、65 个标题的事件条，填了 labels.json。
- 片中有不是博主自己拍的画面：特罗姆瑟城区航拍（简介写明买自版权网站）、片尾太空画面（618–620 标着 From NASA，227 没有标注）。见 STYLE.md 第 3 节。

## 保留了什么

- 仓库里：STYLE.md、style.json、本文件。
- 本地 `<root>/styles/links/first-snow-mountain/stills/`：10 张 320 宽关键帧，只用来做验收对照。
- 本地 `<root>/styles/links/refs/BV1SSQLBREVV/`：measure_video.json、measure_audio.json、labels.json（已去掉转写和字幕原文），用来合并博主级包。
- 参考片本身、代理、抽帧和事件条：2026-11-06 已移进废纸篓；以后要重测，按上面的链接重新下载。

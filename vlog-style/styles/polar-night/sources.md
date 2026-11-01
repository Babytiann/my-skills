# polar-night 的参考来源

| 项 | 内容 |
|---|---|
| 参考 id | BV1sHqPBLExZ |
| 链接 | https://www.bilibili.com/video/BV1sHqPBLExZ （短链 b23.tv/yvSUNvh） |
| 标题 | 当太阳不再升起，我们的世界会变成什么样？第 1 集「0.9 度的太阳」 |
| UP 主 | Linksphotograph（原创） |
| 时长 | 29:46，单个分 P，内嵌字幕 |
| 测量用的版本 | 1080p 29.97 fps 下载，540p 代理 |
| 测量日期 | 2026-11-01 |
| 用到的部分 | 全片。5 个章节参与章节间一致性比较；赞助段（15:55.9–16:45.2）只记结构位置，不进景别统计 |

## 怎么测的

- 画面：TransNetV2 + PySceneDetect 找剪辑点；LK 光流测运动；Lab look card；Apple Vision OCR（每秒 4 帧）。
- 声音：ebur128 响度；beat_this 拍点。字幕已经覆盖旁白，没有跑 whisper。
- 判读：Claude 看 7 张总览图、2 张推拉对照图、20 条转场候选、12 条抽样硬切、15 条标题事件条，以及 6 张 1080p 标题裁切，填了 labels.json。

## 保留了什么

- 仓库里：STYLE.md、style.json、本文件。
- 本地 `<root>/styles/polar-night/stills/`：10 张 320 宽关键帧，只用来做验收对照。
- 参考片本身、代理、抽帧和事件条都在 `<root>/cache/refs/BV1sHqPBLExZ/`。用户确认风格包后，用 `cleanup.py trash BV1sHqPBLExZ` 移进废纸篓；以后要重测，按上面的链接重新下载。

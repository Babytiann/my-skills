# links/uummannaq 的参考来源

| 项 | 内容 |
|---|---|
| 参考 id | BV1fwEwzeEPp |
| 链接 | https://www.bilibili.com/video/BV1fwEwzeEPp |
| 标题 | 【Links】飞跃13000公里，我们抵达了北极的心脏！格陵兰乌玛那刻｜4K杜比视界 |
| UP 主 | Linksphotograph（mid 3816626，https://space.bilibili.com/3816626） |
| 发布日期 | 2025-05-14 |
| 时长 | 28:47（页面），下载实测 1726 s；单个分 P，内嵌字幕 |
| 测量用的版本 | B 站 1080p 档：AV1 1920×1080、29.97 fps、bt709 SDR 8 bit。标题写杜比视界，下载到的这一档是 SDR；540p 代理（960×540） |
| 测量日期 | 2026-11-10 |
| 用到的部分 | 全片。切成 12 段，9 个章节参与章节间一致性比较；没有赞助段。地图 5 张、黑底照片 14 张、黑底卡和 3 个碎片不进景别统计 |

## 怎么测的

- 画面：TransNetV2 + PySceneDetect 找剪辑点（两路一致 315 个，没有被否掉的）；LK 光流测运动；Lab look card（逐个关键帧遮掉镜头自己的黑边）；Apple Vision OCR（每秒 4 帧）。
- 声音：ebur128 响度；beat_this 拍点，librosa 交叉核对（两者对不上）。烧录字幕覆盖了旁白和对话，平台没有字幕，没有跑 whisper。
- 判读：看 8 张总览图、2 张推拉对照图，以及 23 个事件（9 个转场候选、14 条抽样硬切）前后各 8 帧的对照图，填了 labels.json。推拉看不准的取 10%、90% 两帧比较上下半的缩放。

## 保留了什么

- 仓库里：STYLE.md、style.json、本文件。
- 本地 `<root>/styles/links/uummannaq/stills/`：10 张 320×180 关键帧，只用来做验收对照。
- 参考片本身、代理、抽帧和事件条在 `<root>/cache/refs/BV1fwEwzeEPp/`。用户审过 STYLE.md 后先 `cleanup.py keep BV1fwEwzeEPp --style links`，再移进废纸篓。

## 后续

- 2026-11-10：第二批入池（用户按地点挑：极地），和第一批三条一起并进博主级包 `links`。

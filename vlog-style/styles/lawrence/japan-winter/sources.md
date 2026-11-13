# lawrence/japan-winter 的参考来源

| 项 | 内容 |
|---|---|
| 参考 id | BV1Rj421S7c4 |
| 链接 | https://www.bilibili.com/video/BV1Rj421S7c4 |
| 标题 | 遇到ta的概率是六十亿分之一！｜ 日本4k旅拍 |
| UP 主 | 小鹿Lawrence（mid 37029661，https://space.bilibili.com/37029661） |
| 发布日期 | 2024-02-28 |
| 时长 | 4:26（页面），下载实测 265.3 s；单个分 P，烧录字幕，平台没有字幕 |
| 测量用的版本 | B 站 1080p 档：HEVC Main 8 bit 4:2:0，1920×1080（16:9），29.97 fps，约 1.87 Mbps；540p 代理（960×540） |
| 测量日期 | 2026-11-11（判读 2026-11-12 完成） |
| 用到的部分 | 全片。切成 11 段，其中 8 个 chapter 参与章节间一致性比较。景别统计不含胶片框快闪的碎镜头 3–7 和带字幕的黑镜头 11，剩 141 个镜头 |

4–5 分钟的「4k旅拍」短片，日本冬天。简介列的地点有名古屋、岐阜、长野、富山、白川乡、白马、圣高原、河口湖、东京；机器是 Sony ZV-E1、Fujifilm X-H2S 和 DJI Mavic 3 Pro。片子做成一部小电影：开头有片名卡和演职员，旁白的台词按片头署名借自一部日剧，片尾放了一段这部剧的片段。

## 怎么测的

- 下载到的是 HEVC，封装标签是 hev1，Apple Vision OCR 读不了。prep 无损改封装成 hvc1（`prep.json` 的 `remuxed: hev1->hvc1`，见 learnings.md 2026-11-11），画面数据没动。
- 画面：TransNetV2 + PySceneDetect 找剪辑点（146 个剪辑点、147 个镜头）；LK 光流测运动；Lab look card（146 个镜头关键帧，遮掉字幕带和 OCR 框）；Apple Vision OCR（每秒 4 帧，804 条文字轨）。
- 声音：ebur128 响度；beat_this 拍点，librosa 交叉核对（75.3 / 152 BPM，librosa 读成两倍速，算一致）。
- 用 `--no-asr` 跑，没有跑 whisper：烧录字幕覆盖了旁白，人声区间按字幕时间算，speech_share 0.643，最长的无字幕区间 13.7 s（232.7–246.5 s），没有超过 15 s 的空档。
- speech_share 偏高：OCR 把约 70 条左对齐的歌词行也分成了字幕，算进了人声。全部字幕轨合起来 168.3 s（63%），只算居中的字幕轨约 140 s（52–53%，看居中的阈值取多宽）。片尾借用片段自带的字幕（262.0–264.5 s）也算进去了。真正的旁白约占一半，见 STYLE.md 第 11 节。
- 判读：几个子 agent 分头完成，主会话核对：
  - 18 个看不准的推拉，主会话用两帧检验复核（取 10%、90% 两帧，比较上半、下半和整幅的缩放）；
  - 主会话曾把 track 475 补成 time_stamp。写包后看帧确认那是展厅投影幕上拍到的字，不是叠字，2026-11-12 从 labels 和 style.json 删掉了；
  - 主会话还改过调色 summary 的措辞。
- 结果填进 labels.json，拒收 0 条，未核实 0 条。
- 写子包时又看了两张文字核对截图：片名卡、歌名标题、track 475 和片尾重现的合成图，以及三条字幕带裁切。拿它们定颜色、底板和描边，并补上 labels 没有的歌词字幕（见 STYLE.md 炼化记录）。

## 保留了什么

- 仓库里：STYLE.md、style.json、本文件。
- 本地 `<root>/styles/lawrence/japan-winter/stills/`：10 张 320×180 关键帧，只用来做验收对照。
- 参考片本身、代理、抽帧、总览图和事件条都在 `<root>/cache/refs/BV1Rj421S7c4/`。**还没有 keep**：等用户审过 STYLE.md，先 `cleanup.py keep BV1Rj421S7c4 --style lawrence`，再移进废纸篓。keep 时不要带转写和字幕原文。

## 后续

- 2026-11-12：子包写完，等用户审。之后和 lawrence/hokkaido-letter 合并成博主级包 `lawrence`。合并时要统一两个包的叫法：本包的 `film_gate_montage` 就是 hokkaido-letter 的 `picture_in_picture`（黑底圆角胶片框快闪）。

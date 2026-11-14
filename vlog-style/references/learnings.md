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

## 2026-11-14 曲子收尾判断用 dB 直接平均：鼓曲被测成 30 秒的渐弱
- 场景：选曲阶段 1，`music_lib.ending_of` 在合成鼓曲上（100 BPM，只有鼓，满电平到最后一击）
- 做法：1 秒窗口里把每 0.05 秒的 dB 值直接平均，再和主体电平（第 75 百分位）比，找最后一次「够响」的位置
- 问题：鼓点之间的空隙在 dB 上很深，直接平均把整首都拉到主体电平 − 6 dB 以下，`tail_s` 29.8，判成 `fade`。正弦、和弦这类连续的声音看不出来
- 修法：窗口里按功率平均（先换成功率、平均完再换回 dB），主体电平也从平滑后的曲线取。鼓曲回到 `hard`，渐弱和余音两种判断不变；`test_music.py` 加了「鼓点之间很空、打到最后一击」的用例。`intro_of` 一起改成功率平均

## 2026-11-14 试听时边播边切候选音轨：播放中切不了，交付页上也切不了
- 场景：选曲探针 #19、#22，一次性工程 `vlog-style-QA-music`，Studio 21.1.1.10 + MCP 4.15.3
- 做法：计划让用户一直播放，agent 听用户说「换 2」就用 `set_track_enable` 切到候选 2 的音轨
- 问题：两处。一是渲染之后 Resolve 停在交付页，在那里 `set_track_enable` 返回 True 却什么都没改，读回和渲染都证实了。二是用户播放时，切轨调用一直挂着（两条各超过 120 秒），用户一直听到旧的那条；读播放头的调用也挂着，所以 MCP 靠读两次播放头判断「在不在播放」的办法在这台机器上用不了。另外 40 秒的测试时间线在一问一答之间就播完了
- 修法：试听改成「用户先暂停 → 说换 k → agent 在剪辑页切轨并读回 → 用户再播」。切轨前先 `get_page`，不是剪辑页就先切页（只在停着时切）。播放中发出的切轨不会丢，暂停时生效。写进 resolve-routes.md 路线表「选曲试听」和常见坑第 30–32 条；播放类探针用 10 分钟以上的时间线

## 2026-11-14 demucs 的人声轨当「有没有人唱」：口播也算人声
- 场景：选曲阶段 0，30 分钟的 BV1sHqPBLExZ（有大段口播）
- 做法：想用 demucs 分出的 vocals 比伴奏响的秒数占比，判断一首歌有没有人声演唱
- 问题：79% 的秒数人声比伴奏响（伴奏 −6 dB 为界），比测得的人声段占比 64.6% 还高，口播全算进了人声
- 修法：判断演唱时先扣掉 measure_audio 的人声段，只看没有口播的段落；或者再加 CLAP 的「唱歌」对「说话」提示词。measure_music.py 按这个写

## 2026-11-14 CLAP 用 laion/larger_clap_music：文本向量塌成一个
- 场景：选曲阶段 0 试模型，transformers 5.23 和 4.57.6 都试了，cpu 和 mps 都试了
- 做法：计划用这个音乐专用的 CLAP 打标签、算相似度
- 问题：完全不同的提示词（正弦音、粉噪、中文女声、底鼓、钢琴）文本向量两两余弦 0.999，RoBERTa 的 CLS 那一层就已经一样了；权重全部加载、没有缺键，分词结果两个版本相同。真实音频上每个提示词的 AUC 都是同一个 0.80，等于没区分
- 修法：换成 `laion/clap-htsat-unfused`（文本向量两两余弦 0.26，正弦片段对「正弦音」0.75，人声对音乐 AUC 0.974）。`laion/larger_clap_music_and_speech` 也正常（AUC 0.972，但「一首歌」这个提示词混得厉害），留作备选。下了没用的两个模型约 1.5 GB 在 Hugging Face 的缓存目录里，清不清由用户定

## 2026-11-14 暗场景的字幕样本把投影或底板测成了字色
- 场景：lawrence 三条缓存（BV1eoQwYPEeu、BV1Rj421S7c4、BV1S9ZGYqEEr）`--redo strips`，第一次给它们测 `strips.subtitle_samples`，每条 12 个样本
- 做法：`measure_video.text_colour(plate=True)` 取字幕颜色，分类规则同 2026-11-13 那条
- 问题：36 个样本里有 2 个明显测错，都截图核对过。日本 track 38（21.5 s，米白字带暗投影，压在烛光上）测成暗橙褐 #843f0f。misc track 3164（809.7 s，白字压在半透明暗底板上，外圈近黑，bg_L 2.6）测成灰 #525352，底板被当成了字，plate 也成了 None。另有日本 track 437（135.2 s，只出现 0.25 s 的淡入英文歌词，细字半透明）测成 #615e70，算边缘情况。草稿按每条片子样本的逐通道中位数取色，这次三条都还是奶油色（misc 重出草稿是 [0.912, 0.855, 0.667]，和包一致），所以没影响；样本少，或者一条片子的暗场景字幕多时会被带偏
- 修法：还没修（用户 2026-11-14 定：先记下，脚本不动）。修的时候拿这三个样本做回归，别的样本颜色不能变

## 2026-11-13 标题颜色测成了背景色：橙红字压在雪地和深色沙滩上
- 场景：lawrence 第一批，《北海道4k旅拍》（BV1eoQwYPEeu）。片名卡 track 127（82 s）和片尾 track 437（280 s）是橙红衬线字（约 #ff4400），背景是雪地，中间夹着深色沙滩和树枝
- 做法：`measure_video.title_colour` 只按 L 做 Otsu 二分，哪一侧的 L 离外圈背景远就当字，取笔画核心
- 问题：橙红字的 L（约 57）和背景差不多，雪和沙滩各占一侧。127 测成淡蓝白 #d2dde9，437 测成灰 #616369，草稿的 `text.title.color` 跟着错。写包时看截图才发现，手改成 #ff4400
- 修法：新的 `text_colour` 把文字框和外圈一起放到 Lab 三通道上分 5 类：框里占比最大、外圈也有的一类是底；在框里明显比外圈多的是字的候选；候选里取离底最远、又最少贴着底的一类（阴影贴着底板，描边和抗锯齿包着填充色），颜色取这一类里离底最远的 10%。描边改成和每个字旁边的局部背景比，雪地夹沙滩不再误报描边。只读打表：127 → #ff4100，437 → #ff4200；日本 0（深蓝）、111 和 757（白）、misc 2819（深灰压在亮冰上）、四张 Part 卡（白）都没变。test_synthetic 加 `TextColourTest`：橙红字压雪地夹沙滩、白字压暗底、深灰字压亮冰、带黑描边的白字、字幕有没有底板。三条缓存的 strips 里存的还是旧颜色，要不要 `--redo strips` 由用户定。2026-11-14 已重跑并 keep：127、437 都是 #ff4100，日本 0、111、757 和 misc 的六张卡仍和原来一样

## 2026-11-13 叠化带上了甩镜候选的方向
- 场景：lawrence 第一批，北海道 event 0 测成甩镜候选（`whip_dir` right），判读成 dissolve
- 做法：aggregate 给每个剪辑点取方向时，先取标签的，没有就取测量的 `whip_dir`，不管标签最后判成什么类型；汇总时同一类型取最常见的方向
- 问题：北海道和博主级草稿的 dissolve 都写成 direction "right"，日本是 "left"，misc 的急推转场是 "in"。叠化没有方向，schema 里 direction 又只能是字符串，写不了 null，三个包都是手动把这个键删掉的
- 修法：`schema.DIRECTED = [whip, wipe]`，`aggregate.cut_direction` 只给这两种写方向，测量的 `whip_dir` 只在类型是 whip 时才用；`check_style` 碰到别的类型带 direction 就报错。test_tools 加 `DirectionTest`，test_stats 加 schema 的那一条

## 2026-11-13 只有十几个剪辑点、两种算法速度对不上，也判节拍可信
- 场景：misc/lawrence-hokkaido（BV1S9ZGYqEEr），非人声段里只有 13 个剪辑点：命中率 0，基线 0.055；beat_this 测 93.17 BPM，librosa 测 107.7，`tempo_check.agrees` 为 false。草稿照样写了 `confidence: ok`
- 做法：只有对齐结果是 too_few_cuts 一类（门槛是 8 个剪辑点）或 `beats.confidence` 是 uncertain 时，才写 uncertain；`tempo_check` 没人看
- 问题：13 个剪辑点、6% 的基线，随机平移一个都命中不到，命中率说明不了什么；两种算法对不上时拍点本身也不可靠
- 修法：`stats.MIN_BEAT_CUTS = 20`。aggregate 的 `beat_reliable` 每条参考单独判，三种情况算不可信：剪辑点太少、measure_audio 判 uncertain、两种算法对不上（半速和倍速仍算对得上，日本 75.3 / 152 就是）并且命中率不到基线的 1.5 倍。对不上但命中率高出基线很多的照样可信：links 12 条里有 6 条对不上，其中 5 条的命中率是基线的 2.8–5.3 倍，剪辑点跟着 beat_this 的拍点走。过半参考可信才记 ok，数值只用可信的几条算；每条的判断和原因写进 `evidence.beat`。结果：北海道 ok，日本、misc、lawrence 是 uncertain，links 仍是 ok（命中率 0.203 → 0.209，去掉了斯瓦尔巴）。行为变化：misc 包重新 draft 后不卡点（原来按 0 的命中率卡，实际也不卡）。速度比较挪进 `stats.tempo_agrees`，test_stats 和 test_tools 各加了测试

## 2026-11-13 博主级合并只取一条参考的写法，字幕描边写死，几种卡片混着取中位数
- 场景：lawrence 博主级包（北海道 + 日本），回看 links 博主级包（12 条）
- 做法：
  - grade.summary、grade.grain、audio.mood 取第一个非空值；selection.opening 后面的覆盖前面的；selection.reject 直接拼接
  - `text.subtitle.stroke` 写死成 true
  - `text.title` 和 `chapter_card` 把所有角色对得上的卡池化取中位数，颜色取十六进制字符串的众数
  - grade.look 每项取各条中位数，一致性只查四项，不删任何一项
- 问题：
  - 博主级草稿照抄了北海道判读里写的音乐人名字，opening 只剩日本的写法，reject 有重复
  - 三条参考看过去都没有描边，links 的字幕是浅灰字加半透明暗底板、没有描边，定稿却写着 stroke true（2026-11-13 打表才发现；2026-11-14 links 的 13 个包都改成 stroke false、plate true，缓存已清的 3 个子包是推断的）
  - lawrence 两个子包的字幕底板，一个写没有、一个写有，各只看了一种行。2026-11-14 用字幕测量逐条看，两条短片其实一样：中文单行不垫底板，带日文第二行的双语台词行垫深灰半透明底板。japan-winter 的 plate 改成 false，三个包的 STYLE.md 改写
  - 日本一条片子里有三种卡（开场片名卡 y 0.33、字高 9.9%；歌名段落标题 y 0.51、27.7%；片尾重现在画面上部），中位数哪一种都对不上；misc 的 chapter_card 混了黑底笑点卡、四张 Part 卡和手写段落标题，算出位置 [0.62, 0.667]、offset 67.33 s
  - look 照抄中位数，博主级包手改时删掉了 chroma、sh_b、mid_b、hi_b 和 hue_hist
- 修法（aggregate.py）：
  - 参考之间写法不同的文字不挑一条：summary、mood 写空串，grain 写 null，opening 不写，记进 `evidence.unverified`；原文只打在 stderr，不进 style.json。只有一条参考时照旧
  - reject 按原文去重
  - 字幕的颜色、描边、底板改成测：strips 阶段在字幕线上（居中、在字幕带上、字高够）均匀取最多 12 条，记 `strips.subtitle_samples`；每条参考七成样本一致才算有或没有，看不出底板的（上方画面太暗）不投票，参考之间不一致就不写。老的测量没有样本，也不写，记进 unverified
  - 标题卡的字高、位置、颜色、时长先按视频取中位数，再在视频之间取；颜色按通道取，参考之间 ΔE > 20 就不写。出现时间（offset_s）照旧把各章开头的卡池化：按视频取中位数时，links 有的片子卡多在章首、偶尔在章中，算出 8.3 s，比池化的 0.2 s 离定稿更远。一条片子里字高差 1.5 倍、或 y 差 0.15 以上的，stderr 逐张列出，记进 `evidence.text_spread` 和 unverified，不自动挑
  - look 只留各条一致的项（`agreeing_look`）：2–3 条时比值类极差不超过合并中位数的 25%、色偏类相差不超过 2；4 条以上每条都在合并中位数 ± MAD 以内（links 包手改时用的规则）。hue_hist 不合并。去掉的项和各条的值写进 `evidence.look_dropped`。北海道加日本正好得到定稿的 9 项；links 草稿留 L01、chroma、vignette，L50 和 shadow 因为极夜两集出界被去掉（定稿按内容加回来了）
  - `look_text` 没有 mid_b 时不再写「中性」
  - `grade.py solve` 和 `compare.py look` 拿缺项的博主级包当 look_ref 本来就不会崩，只是不说；现在 grade 的章节警告和 compare 的输出（`skipped`）都会列出缺的项，提示用子包
  - 测试：test_tools 加 `MergeTest`，test_synthetic 加缺色偏项的调色测试，原有期望值都没有改

## 2026-11-13 子包里手加的特效进不了博主级包
- 场景：lawrence/japan-winter 写包时往 style.json 里直接加了 9 条特效：time_stamp（镜头 12）、end_card（145）、film_credits（145）、lyric_subtitle ×6（镜头 32、46、59、63、96、129）
- 做法：aggregate 的 effects 只从各参考的 labels.json 来
- 问题：博主级草稿里没有这 9 条，写 lawrence 包时手动补的。下次重新 draft 还会丢
- 修法：
  - 规定写包时发现的特效先补进 labels.json，再重新 draft（measure.md 判读约定、SKILL.md 第 4 节第 7 步）
  - `aggregate.py draft` 拿只用这一条参考的子包或 misc 包（`--packs`，默认仓库的 styles/）比一遍，子包有、labels 没有的逐条警告，写包时改了名的标出 labels 里的名字，并记进 unverified
  - 多参考合并时每条特效带 `ref`
  - 日本的 9 条按用户同意补进了 `cache/refs/BV1Rj421S7c4/labels.json`（先备份成 `labels.json.bak-20261113`，note 后面注了来历），重新 draft 后日本的 effects 和定稿一致（17 条），lawrence 也是 34 条，只差北海道镜头 3 的改名（labels 叫 picture_in_picture，定稿叫 film_gate_montage）
  - misc 还有 6 条改名（framed_clip_inset → picture_in_picture ×5，end_credits → film_credits）会被警告列出来
  - test_tools 加 `PackOnlyEffectsTest`

## 2026-11-11 B 站的 HEVC 标成 hev1，OCR 一帧都没读到，却照常退出 0
- 场景：lawrence 第一批，《日本4k旅拍》（BV1Rj421S7c4）。下载到的是 HEVC，视频轨的 codec tag 是 `hev1`。之前的参考都是 H.264 或 AV1
- 做法：`vision_ocr.swift` 用 AVFoundation 逐帧取图，取不到的帧只在 stderr 记前 5 条，最后照常写出 ocr.json，退出 0
- 问题：AVFoundation 只认 `hvc1` 标的 HEVC，1062 帧全部「Cannot Open」，ocr.json 是 0 条文字。ffmpeg 读得了 hev1，所以 prep 的代理和 measure_video 都照常跑完，片子看起来就像没有一个字。排队脚本记下的 OCR 耗时是 0.0 分钟，这才发现
- 修法：`prep.py ref` 下载完，如果视频轨是 hev1 标的 HEVC，就原地转封装成 hvc1（`-c copy`，帧和时间戳不变），prep.json 记 `remuxed: "hev1->hvc1"`。用户的本地文件照旧不动。`vision_ocr.swift` 一帧都解不出时报错退出 2，给出转封装命令，不写 ocr.json。test_tools 加 `Hvc1RemuxTest`。这条参考按新的 prep 重跑了取片和 OCR。注意：measure_video 不加 `--redo` 时会沿用已有的各阶段，看不出 ocr.json 换过了；重跑 OCR 之后要跑 `--redo text`（这次就是不带 `--redo` 跑了一遍，文字还是空的）

## 2026-11-11 keep 不认 subtitle_misread：第二批三条的保留副本留着字幕原文
- 场景：links 第二批收尾，拿缓存里的字幕原文搜 `<root>/styles/links/refs/` 下所有保留副本
- 做法：2026-11-06 那条的修法是「keep 之后按 note 手动清空」，第二批照常 `cleanup.py keep`，手动这步没人做
- 问题：北海道 1 条、隐岐 2 条、斯瓦尔巴 2 条记了 `subtitle_misread` 的标题，整句字幕原文还留在三处：labels.json 的 `titles[].text`、measure_video.json 的 `text.tracks[].text`，和之前没注意到的 `strips.titles[].text`。罗弗敦另有一截被 OCR 拆出来的字幕（T580）判读时记成了普通的 other，同样漏了
- 修法：`cleanup.py keep` 自己清：`strip_labels` 清空记 `subtitle_misread` 的标题文字（标签和 note 留着），`strip_video` 按这些 track 清空 `text.tracks` 和 `strips.titles` 里的文字。test_tools 加 `test_keep_blanks_captions_that_ocr_sorted_as_titles`。北海道、隐岐、斯瓦尔巴重新 keep（缓存里的测量和保留副本逐项比过，只有北海道多了后来加的 `shots_with_bars`，数值一样）；罗弗敦 T580 改记 `subtitle_misread` 后 keep。之后拿缓存原文（6 字以上的字幕行）搜 9 条有缓存的保留副本，0 处；第一批三条缓存已清，没法这样搜，它们的误判标题文字已经是空的

## 2026-11-10 画面下方的场景字太多，字幕带取到了字幕上面
- 场景：links 第二批，斯瓦尔巴「最北的城市」（BV1H5xie8EJZ）。全片烧录字幕在 y 0.95、字高约 4.2%；另有大量小字在画面下部：约 10:00 一段录屏的订票网页（价格、行程名，字高 1.3%），加路牌、店招
- 做法：`stats.classify_text` 取所有「下方居中、不漂」的文字的 y 中位数当字幕带中心，再取带内字高中位数
- 问题：2216 个候选里字幕约 700 个，其余分散在 0.7–0.97，中位数落到 0.884，带里多是 1.3% 的小字。真字幕比这个高 1.6 倍以上被排出字幕带，又因为比「字幕」高被归成标题：字幕 383、标题 915。标签骨架里 915 个标题、68 个提议章节，没法判读。看分类计数时发现（其余 8 条的标题都在 51–179）
- 修法：字幕带中心改取「±0.02 内文字最多的那一行」再取中位数（stats.py），不再取全部候选的中位数。斯瓦尔巴变成字幕 744、标题 95；其余 9 条参考（含 lonely-house）重算对照，只有青森 2 条、极夜第 1 集 1 条、lonely-house 1 条从字幕变成 other，已经炼完的子包没有重测。斯瓦尔巴 `--redo text`（27 秒）后重建了标签骨架（95 个标题、33 个提议章节）。test_stats 加 `test_band_sits_on_the_caption_line_not_on_scene_text`

## 2026-11-10 低帧率步进镜头被算成一串 0.53 s 的镜头
- 场景：links/kyoto（BV1ym42137vx），夜里进林子那段，画面每 8 帧才更新一次（约 3.75 帧/秒）
- 做法：PySceneDetect 找到的剪辑点只要帧差有跳变就收（`cuts` 的交叉核对）
- 问题：步进镜头每次更新帧差都会跳一下，PySceneDetect 每隔一次更新报一个剪辑点，9 个都被收了，镜头 285–295 变成 10 个 0.53 s 的碎片；同一段里另有 15 个被否掉。全片中位数 3.67 → 3.55 s，`min_shot_s` 1.67 → 1.06 s。抽样硬切的事件条（e123）前后是同一个画面，才发现
- 修法：没有改测量（只此一处，改 cuts 要重跑整条测量、标签全部重标）。判读时看帧差：一串间隔相同、中间全是 0 的小跳变是步进，不是剪辑；这类碎片多到影响 `min_shot_s` 时按合并后的镜头手改 `rhythm`，写进炼化记录。measure.md 判读约定跟着改。以后再遇到，考虑在 cuts 交叉核对里认出等间隔的步进

## 2026-11-10 只有部分镜头带黑边，look 把黑边算进了暗部
- 场景：links 第二批，隐岐诸岛（BV1ydjizBE1o）293 个镜头里 92 个风景和情绪镜头上下烧进约 12% 的黑边（2.35:1），说话段是 16:9
- 做法：look 只遮全片统一的遮幅（`scan.letterbox` 按全片所有帧找，这条片子是 0），单个镜头的黑边照样算进 Lab
- 问题：带黑边的镜头黑位 L01 中位数 0、暗部占比 0.36，不带的是 2.96、0.18；全片 L01 被拉到 1.62，暗角 0.82（实际约 1.0）。只用不带黑边的镜头算又会把风景镜头全排除，look 只剩说话段。第一版检测用「整行纯黑」，字幕正好压在下面的黑边上，35 个镜头的黑边被字幕截短、上下不对称，没认出来；第二版先遮黑边再判「全黑画面不计」，黑底字卡遮掉黑边后只剩字，被当成画面算了进来
- 修法：`measure_video.py` 加 `frame_bars`，逐个关键帧找纯黑边：一行六成以上是纯黑、中位数 ≤ 3 就算黑边行（字幕压在黑边上也行），上下或左右要成对、对称、每边 3%–35%，黑边里过半的行完全纯黑，黑边以内要有画面。先按原来的遮罩判断要不要跳过，再去掉黑边算数值，所以没有黑边的片子输出和改动前完全一样（北海道、京都、极夜第 2 集已核对）。`per_shot` 多一项 `bars`。第二批 9 条都 `--redo look` 重测过（只重算 look，0.7–1.1 秒一条）。test_synthetic 加 `FrameBarsTest`。第一批三条 keep 下来的测量是改之前测的，黑底照片插入的黑边算进了暗部；它们的抽帧已经清掉，没有重测

## 2026-11-10 2:1 的参考片：文字位置按原片记，放进 16:9 会落到黑边上
- 场景：links 第二批，北海道、京都、罗弗敦三条下载下来是 1920×960（2:1），交付是 3840×2160
- 做法：aggregate 只认烧在画面里的黑边（`scan.letterbox`），文件本身比 16:9 宽就当成没有遮幅；字幕、标题的位置和字高直接按原片画面的比例写进 style.json
- 问题：北海道的字幕中心 y 0.963、字高 5.0%，是 2:1 画面里的比例。放进 16:9 时间线、上下加遮幅之后，画面只占中间 88.9%，下面的黑边从 0.944 开始，照原数摆字幕会压在黑边上，compare.py 拿来比的也是这个数；letterbox 又是 null，Resolve 根本不会加遮幅
- 修法：aggregate.py 加 `frame_map`、`picture_aspect`、`pooled_letterbox`。文件比交付画幅宽 2% 以上就按遮幅算，letterbox 记它的宽高比，标题、章节卡和字幕的 y 和字高换算到交付画面（y' = pad + k·y，北海道的字幕变成 0.912、4.44%）；多参考合并时过半的参考是这样才写 letterbox，少数时记进 `evidence.letterbox`。test_tools 加 `WiderPictureTest`；apply.md 第 7 节第 2 步加遮幅，style-schema.md 的 deliverable 跟着改

## 2026-11-10 root 搬到外接盘，第一次写入卡了 8 分钟：系统在等授权
- 场景：`paths.toml` 的 root 改到外接硬盘上的 `/Volumes/<卷名>/vlog-style`，用 `ditto` 把旧 root（5.6 MB）复制过去，macOS 27.2
- 做法：直接复制，接着跑 `diff` 和 `paths.py show`；复制没返回，又另开了一个 `ls` 看这个卷
- 问题：`ditto` 停在 `mkdir` 上，没有任何输出，`ls` 也不返回；`df` 和 `mount` 却正常，内核日志里没有硬盘错误。原因是 macOS 要用户点一次「可移除的卷」授权，弹窗没点之前，读写这个卷的命令都在等。用户点了之后，复制自己跑完，两边逐文件一致。同一天早些时候只读地列这个卷是通的，所以一开始没往授权上想
- 修法：没有改代码。卷上的命令不返回时不要再起第二份：先用 `df` 确认卷还挂着，再请用户看屏幕上有没有授权弹窗；`log show --last 10m --predicate 'process == "tccd"'` 里能看到 `kTCCServiceSystemPolicyRemovableVolumes` 的请求。`paths.py` 的「卷没挂载」检查管不到这种情况，因为卷是挂着的

## 2026-11-07 成片的字幕框高：带逗号的句子框更高、更低
- 场景：探针 #17，`vlog-style-QA-probe17`，Studio 21.1.1.10 + MCP 4.15.3，Simple White、PingFang SC Regular、`size 0.040`、`position [0.5, 0.032]`，H.265 烧录
- 做法：`compare.py final` 取读到的所有句子的框高、中心 y 中位数，和风格包的 `text.subtitle`（links：4.44%、0.955）比，±15%、±0.015
- 问题：5 句里 3 句带逗号（subs.py 保留了 whisper 的半角逗号），框高 5.21–5.60%，不带的两句 4.72%；中位数 5.21%，超了 17%，好片子被判字号不对。参考片留下的 1371、1048、761 条字幕轨迹里都没有标点，两边量的不是同一种句子
- 修法：`check_subs` 只用不带逗号、分号、顿号、括号和 g/j/p/q/y 的句子量框高和中心（`LOW_INK`），一句都没有才全用，问题里注明「每句都有逗号或下伸笔画」。输出多一个 `measured_on`。改完好片子 6 项全过；坏版本各自只挂对应一项。test_tools 加了按实测值写的回归测试，apply.md §7 第 13 步和 §9 跟着改

## 2026-11-07 mix.py master 差 0.1 LU 也让人上限幅器
- 场景：探针 #17，同上，只出音频的渲染两轮
- 做法：真峰值压着上不去时，`apply_master` 只要比 `--lufs` 少一点就报「差多少 LU，在 Fairlight 主母线上加限幅器（手动收尾）」
- 问题：第二轮实测 −14.1 LUFS、真峰值 −1.0，差 0.1 LU，也报了要上限幅器。`compare.py final` 允许 ±1 LUFS，这点差没有意义，还会让人白做一步手动收尾
- 修法：差 0.5 LU 以上才报（`SHORT_LU`），`expected` 照样写实际能到的值。test_tools 加了 −14.1、−14.4 不报、−14.6 报的回归测试，apply.md §7 第 12 步跟着改

## 2026-11-07 CDL 和 LUT 写在同一个节点，CDL 跑到了 LUT 前面
- 场景：探针 #16，`vlog-style-QA-probe2`，Studio 21.1.1.10 + MCP 4.15.3，逐通道的测试 LUT
- 做法：apply.md 的例外原来写的是先 `graph set_lut` 到节点 1，再 `safe_set_cdl`，NodeIndex 照默认写 1；探针 #9 的 DRX 配方也是 LUT 套到节点 1
- 问题：静帧对上的是「先 CDL 后 LUT」（平均差 0.30/255），和「先 LUT 后 CDL」差 3.5/255。可 `--stills` 是在套好 LUT 的静帧上解 CDL，假设的是 LUT 在前。另外重套 DRX 之后，已写的 CDL 没了，静帧和未调色的逐像素相同
- 修法：中性 DRX 再 merge 一个空节点，LUT 放节点 1，CDL 放节点 2，静帧对上「先 LUT 后 CDL」（0.25/255）。`grade.py solve` 加 `--node`（默认 1），例外时传 2，`resolve` 块里的 NodeIndex 跟着变。只有 LUT 时也建两个节点；CDL 一定在 DRX 之后写。写进 apply.md §7 第 11 步、resolve-routes.md 路线表和常见坑第 29 条

## 2026-11-07 按章调色不走 DRX，CDL 用 safe_set_cdl 直接写
- 场景：阶段 3 按章调色，离线 server `drx cdl_io`，MCP 4.15.3（读源码，没探针）
- 做法：需求文档原来的写法是每章生成一个 DRX：`drx generate` 中性打底，`drx cdl_io` 把 CDL 换成 DRX 节点再 `drx merge`，每个条目 `safe_apply_drx`
- 问题：`cdl_io` 把 slope 当 gain、offset 照搬、power 当 gamma、sat 从 0–2 换成 0–100，源码注释自己写着是近似换算。grade.py 按 ASC CDL 公式预测的 look 和渲染出来的会差一截，还得另做补偿。探针 #9 里的「CDL」节点用的也是 gradeParams 的 UI gain 和 saturation，不是 ASC CDL
- 修法：grade.py 输出精确的 ASC CDL 和现成的 `safe_set_cdl` 参数（`resolve` 块），每个 V1/V2 条目写一次。这也是片场按场出 CDL、调色师套用再细修的常见做法。#13 验证过 slope，#16 验证过 offset、power、sat 和分通道组合。DRX 只留给风格包自带的 LUT、DRX、颗粒（apply.md §7 第 11 步的例外）

## 2026-11-07 照片的拍摄时间不能用 sips 和 Spotlight 读
- 场景：阶段 2 照片，macOS 自带的 sips 和 mdls
- 做法：打算用 `sips -g creation` 或 `mdls kMDItemContentCreationDate` 拿拍摄时间，免得自己解析 EXIF
- 问题：sips 的 `creation` 只读 TIFF 的 DateTime，相机写的 DateTimeOriginal 读回 `<nil>`；mdls 给的是这份文件的创建时间，复制一次就变成复制的时刻。EXIF 的时间本身不带时区，要另读 OffsetTimeOriginal。用户发来的一张实拍（经聊天转发）只剩 OffsetTimeOriginal、镜头和亚秒，拍摄时间和机型都被去掉了
- 修法：sips 生成预览时会保留 EXIF，`prep.py` 用标准库从 preview.jpg 读 DateTimeOriginal 和 OffsetTimeOriginal，HEIC 也一样。没有时区的按本机时区读，设备名加「(no time zone)」，好单独设时钟偏移；没有拍摄时间的照片会提醒换原片或在 tags.json 写 `shot_at`。方向用 EXIF Orientation，ffmpeg 9 解 JPEG 时自己转正，sips 转 HEIC 也保留这个标签

## 2026-11-07 整条片一次转写，prompt 只管得到第一个窗口
- 场景：测试 A，孤岛（BV1uparzhEjs，29 分钟），mlx-whisper large-v3-turbo，`measure_audio.whisper()` 原来的写法
- 做法：整条声音一次 `transcribe`，`initial_prompt` 写简体和标点，`condition_on_previous_text=False`，把 whisper 的 segment 当句子用
- 问题：mlx_whisper 在每个 30 秒窗口之后重置 prompt（`prompt_reset_since`），第一个窗口之后就没有标点，也不保证简体。1121 段里只有 24 段带句末标点，37% 的字幕行被切进两段，segment 中位数 1.0 秒，只是半句；另有 462 个边界附近没有字幕行（音乐段里的幻听）。打开 `word_timestamps` 也一样，而且 93.5% 的词间隔是 0，靠停顿切不出句子
- 修法：`measure_audio --sentences`：在静处把声音切成不到 30 秒的段，每段都带 prompt 和词时间戳转写，再按句末标点、长停顿、换语言拼成句子（`stats.quiet_cuts`、`stats.sentences`）。句末标点 95%，被切开的字幕行 6%，29 分钟音频 231 秒。apply 默认加，炼化不加，输出不变

## 2026-11-07 自动判语言时，中文 prompt 把外语转成了中文
- 场景：同上，`--language auto`
- 做法：每段自动判语言，prompt 照旧用中文那句
- 问题：判成英语的 45 句文字全是中文，而且是重复的粗糙翻译（「全部都在我的手」连说三遍）。写死 `zh` 时，外语对话也会变成这种乱码
- 修法：判成非中文的段去掉 prompt，按判出的语言再转一次（整条只多 2 秒），101 句英语都是原文。语言作为第 4 项写进 footage.json，字幕由 agent 翻译，写进 subs_fixes.json 的 `translate`。一段里混着两种语言时仍然只按一种转，apply.md 写明了

## 2026-11-07 字幕字号按笔画高度估，烧出来偏大
- 场景：探针 #15，`vlog-style-QA-probe2`，Studio 21.1.1.10 + MCP 4.15.3
- 做法：links 和三个子包的 STYLE.md 按 polar-night 那次探针的比例估算，写的是 `size 0.053` + `position [0.5, 0.023]`
- 问题：风格包的字高是 `vision_ocr` 的框高，polar-night 那次量的是笔画。PingFang SC 按 0.053 渲染，OCR 框高 6.1%，比目标 4.4% 大四成。另外 `set_subtitle_preset` 不给预设名直接拒绝，也写不了描边
- 修法：两次渲染拟合出「框高 ≈ 1.1 × size」「中心 y ≈ 0.9875 − position y」，`size 0.040` + `position [0.5, 0.032]` 实测框高 4.44%、中心 y 0.9555。各 STYLE.md 的换算行和 resolve-routes.md 常见坑第 28 条改过，预设用 `Simple White`

## 2026-11-07 原生静图按帧放不出想要的长度
- 场景：探针 #14，同上
- 做法：jpg、png、heic 直接导入，append 时 `end_frame` 给 120、240、72
- 问题：读回三条都是 150 帧，即偏好设置里默认的静图时长 5 秒，`start_frame`/`end_frame` 不起作用。改偏好设置要先问用户，而且照片长短不一，一个默认值也不够
- 修法：照片先用 ffmpeg 做成时间线帧率、等比缩放、标 bt709 的短片再按帧放，75 帧和 240 帧都对，缩放生效，色差可以忽略。写进 resolve-routes.md 路线表「照片」和常见坑第 27 条。同一轮实测还发现常见坑第 24 条过时了：`folder get_clips` 会列出时间线条目的 id

## 2026-11-06 素材不够时只扣分，会把后几天的事搬到前面
- 场景：规划 plan_cut 按拍摄时间选镜头，用户评审计划。需求文档原来写的是往回选扣分、不得不往回选时给 warning
- 做法：往回选的镜头只扣分；素材不够时 `any_tags` 回退到全部素材（`plan_cut.py` 原来的写法）
- 问题：第一天素材不够时，第三天标签和画面都更好的镜头会被拿来填，检查还能通过。用户：「三天后的素材……放到第一天来，那这是完全不对的」。改成一律禁止又太死：「单纯的风景运镜，必要时还是可以的」
- 修法：镜头分两类。默认都有时间进展，不能离开本章的时间窗；tags 里标了 `movable` 的纯风景，在同地点、光线相容时才能借，借了在表里标「借用」。还不够就报错，写明差多少秒，不悄悄回退。例外（预告、片尾照片、赞助段、`out_of_order`）要列名，`check_plan` 再独立查一遍

## 2026-11-06 `trash --all` 会带走还没测完的参考
- 场景：第三条还在跑 OCR，另一个会话在规划清理 links 的下载缓存
- 做法：`cleanup.py trash` 只靠 measure_video.json 判断要不要先 keep
- 问题：还没跑完 measure_video 的参考没有这个文件，保护不起作用，`trash --all` 会把它和别的缓存一起移走，正在跑的任务就断了。按 id 清也一样，只是不容易点错
- 修法：`trash` 默认挡住「没有 labels.json 也没有保留副本」和「有进程正在用」的参考，`--all` 跳过并列在 stderr，按 id 时拒绝。有意放弃的下载加 `--unfinished`；正在用的一律不动。`list` 加了 `state`。有保留副本的回看下载不算没测完

## 2026-11-06 博主级 draft 能跑通，但有几项不能直接用
- 场景：links 博主级包，三条真实参考第一次合并
- 做法：直接用 `aggregate.py draft` 的结果
- 问题：`summary` 是空的；`grade.summary`、`audio.mood`、`selection.opening` 只取单条；`text.title` 的位置和 `offset_s` 按标题个数算，一条片子的 7 个标题压过另外两条，`offset_s` 成了 34.78 s（plan_cut 会把标题推迟到段落开始后这么久）；`grade.look` 带着三条相差很大的色偏；`transitions` 带着 `other`（拍摄时做的手挡镜头），plan_cut 按占比可能抽中，Resolve 又搭不出来
- 修法：逐项手改，写进 links/STYLE.md 的炼化记录。标题的位置和时机取每条视频的中位数；look 只留三条一致的项；`other` 从 `transitions` 去掉，计数留在 evidence。写下一个博主级包时照这个清单过一遍

## 2026-11-06 STYLE.md 里顺手写的数和定性说法没有对过测量
- 场景：links/first-snow-mountain 和 links/kula-kangri 写完之后复核
- 做法：照上一条的模板写，「多数」「大部分」这类话凭判读时的印象
- 问题：「地图多数用 15–20 帧叠化进出」实际是 9 个里 5 个、14–20 帧；「对话撑起大部分时长」实际中景按时长占 49.9%；kula 写的「185 个稳定前进或后退」实际是 104
- 修法：写完用脚本按 qa.md 逐条核，尤其是「多数」这类词，能换成数字就换

## 2026-11-06 下载超过 60 秒，opencli 就报失败，文件其实是完整的
- 场景：links/first-snow-mountain（55:12，1.1 GB），`prep.py ref` 调 `opencli bilibili download`
- 做法：只按 opencli 的退出码和留下的文件判断下载成败，报错时只显示 stderr
- 问题：两次都报 download failed，stderr 结尾却是 `Download complete`，两次的文件逐字节相同，完整解码 0 错误。opencli 的浏览器命令默认 60 秒超时（`OPENCLI_BROWSER_COMMAND_TIMEOUT`），超时只报错、不中止下载，错误打在 stdout 上。前两条下载 50–58 秒没碰到，这条要 106–132 秒
- 修法：`prep.py` 下载时把这个超时设成 `DOWNLOAD_TIMEOUT_S`（3600 秒），用户自己设过的值优先；失败时报退出码，stdout 和 stderr 都带上。重下一次退出码为 0

## 2026-11-06 OCR 读到的 `/bpm` 被 paths.py check 当成绝对路径
- 场景：links/kula-kangri keep 之后跑 `paths.py check`
- 做法：JSON 里凡是以 `/` 开头的字符串都算绝对路径
- 问题：片子里指夹血氧仪外壳上印的 `PR /bpm`，`/bpm` 被 OCR 读成了两条标题轨，在 measure_video.json 和 labels.json 里一共报了 6 个绝对路径，其实一个也没有
- 修法：OCR 文字字段（`text.tracks`、`text.transcript`、`strips.titles`、`titles`、`samples` 的 `text`）以 `/` 开头、又不含真实文件系统根目录的值，`check` 只列成提示，不计数。其他字段照旧

## 2026-11-06 字幕被 OCR 判成标题，keep 之后原文还留着
- 场景：links/kula-kangri，`cleanup.py keep` 之后查保留的测量里有没有字幕原文
- 做法：只拿 kind 为 subtitle 的轨去搜
- 问题：keep 只清 kind 不是 title 的轨。T2357 是一行字幕，OCR 判成了 title，判读时标了 other、note 记 subtitle_misread，14 个字在 measure_video.json 和 labels.json 里共留了 3 处
- 修法：判读时把误判成标题的字幕记成 note `subtitle_misread`，keep 之后按这个 note 把保留副本里的对应文字清空，再拿缓存里的原文搜一遍，搜不到才算过。2026-11-11 起 keep 自己清（见上面那条），手动这步不用做了，原文搜索还要做

## 2026-11-05 PySceneDetect 和 ffmpeg 的帧号在长片上会错开
- 场景：links/lonely-house（29:22），第一次在长片上跑 measure_video
- 做法：把 PySceneDetect 给的帧号直接和 ffmpeg 管道里的帧号比较
- 问题：ffmpeg 的 rawvideo 管道是恒定帧率，时间戳跳的地方会重复一帧；PySceneDetect 用 OpenCV，只数解码出来的帧。代理约每 240 s 跳一帧，全片错开 7 帧。结果两路一致的剪辑点只有 116 个，否掉 147 个 psd 剪辑点，多出 29 个 0.5 s 以下的重复镜头。从碎镜头数量异常发现，比对两路的帧号确认
- 修法：`measure_video.py` 用 `proxy_clock` 读代理的时间戳，`stats.cfr_index` 把 PySceneDetect 的帧号映射到恒定帧率时钟，错开的帧数记进 `cuts.psd_clock_shift`。重测后一致 296 个，否掉 0 个，碎镜头 5 个

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
- 修法：测量只负责「稳不稳」（`zoom_eff` ≥ 0.95，排除人走过、转身造成的假推拉）；数字还是机位由 agent 看 `strips/moves_*.jpg` 判，aggregate 只按 digital 算推拉比例

## 2026-11-01 黑场判定只看两侧基线：极夜片里大量误报
- 场景：同上，measure_video.py 的 luma_event / snap_cuts
- 做法：最低亮度 < 0.06 且两侧基线中位数 > 0.12 就算黑场，剪辑点挪到黑场中点
- 问题：13 个黑场候选里 6 个是硬切进暗镜头，剪辑点被挪偏 10–12 帧；黑底字卡两头的硬切被挪到一起，字卡从 23 帧变成 5 帧。看事件条发现
- 修法：黑场、闪白在 0.4 秒窗口内两头都要回到基线才算；snap 每次从 `cuts[].raw` 算起。test_synthetic.py 加了 LumaEventTest

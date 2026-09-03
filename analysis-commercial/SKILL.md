---
name: analysis-commercial
description: >-
  Orchestrates 1-6 month swing decisions for single stocks, sector or theme ETFs,
  and long-term accumulation pacing for broad indexes such as the S&P 500 and
  Nasdaq 100. Use when the user asks 现在能买吗, 为什么涨跌, whether a name fits a
  multi-month 波段, or whether 标普 / 纳指 is worth long-term 配置. Builds an evidence
  ledger first, then separates causality, confidence, and trade action. Hands
  decade-long value judgment to investment-research and 10-minute intraday
  attribution to news-pulse. Never invents figures, never treats correlation as
  causation, and never upgrades a losing swing into a long-term hold.
---

# 股票波段与宽基配置分析

`analysis-commercial` 是唯一最终决策层。现有投研 Skills 的方法论已被吸收，运行时不要再叠加执行它们。

## 何时用，何时让渡

使用本 Skill：

- 单只股票、行业或主题 ETF 的 `1–6` 个月波段
- 标普 500、纳斯达克 100 等宽基的长期配置节奏
- “现在能买吗 / 为什么涨跌 / 适不适合做几个月波段 / 标普纳指要不要长期配置”

让渡：

- 十年价值判断、护城河和内在价值 → `investment-research`
- 盘中约 10 分钟异动归因 → `news-pulse`
- 管理层人格或组合再平衡本身不是本 Skill 的主问题 → 只吸收其检查清单，不转交决策

个股不输出“长期值得买”。个股长期质量只作风险背景，不能单独否决一个证据充分的波段，也不能把亏损波段改口成长线。

## 开工三件事

1. 用系统日期确认“今天”，写入报告头。禁止用训练记忆里的日期。
2. 问清或读取：净资产与币种、已有持仓清单（标的 / 方向 / 1R / 主题 / sleeve）。缺任一项，最高输出 `等待验证`。
3. 分流模式与袖：

| 问题 | 模式 |
|---|---|
| 该标的尚无本地档案 | 全量建档 |
| 已有档案且用户只问增量 | 快速更新 |
| 个股 / 行业或主题 ETF | 战术波段袖 |
| 标普 / 纳指 / VOO / QQQ 等宽基长期 | 宽基袖 |
| 宽基被拿来做 1–6 个月波段 | 按战术袖计 |

## 防幻觉三铁律

1. 账本里的每个数字必须带来源 URL 和检索日期。禁止用训练记忆填财务或价格。
2. 仓位、亏损、盈亏比、出清天数、主题热量一律跑 `scripts/trade_math.py`。报告数字逐字引用脚本 JSON。
3. 价格、估值、空头数据超过 1 个交易日即过期。过期数据不得支撑 `可执行`。

付费源不可用时找免费等价证据；仍缺则降低结论，不要编造。

## 授权与禁令

- 美股现货可多可空；港股 / A 股默认只做多，除非用户明确有可执行的融券通道。
- 做空必须比做多保守：单笔计划风险 `0.5%`，战术空头合计风险 `≤2%`，必须有借券费，高逼空禁止。
- 禁止杠杆、保证金放大、期权、反向或杠杆 ETF。名义敞口不得超过净资产。
- 禁止保证收益、虚构胜率百分比、把相关性写成因果、漏写止损、推荐单股长期持有、把短线亏损改成长线。

## 执行顺序

按这个顺序，不要跳步：

```
模式分流 → 账本授权 → 分阶段采集并落盘 JSONL
→ evidence_gate.py → 三层分析与因果四档 → 红队
→ trade_math.py → 按有仓/无仓输出动作
```

`evidence_gate.py` 失败 → 只能 `等待验证`，无例外。
`trade_math.py` 因组合、容量、缺口、ATR、借券或倒推目标拒绝 → `不交易` 或 `不交易（组合约束）`。
宽基结论不得回写成个股 `可执行`。

细节按需读取，不要一次读完：

- 分析顺序 → [analysis-framework.md](references/analysis-framework.md)
- 采集、来源、账本 → [information-collection.md](references/information-collection.md)
- 因果与已定价 → [causal-evidence.md](references/causal-evidence.md)
- 波段与退出 → [swing-trading.md](references/swing-trading.md)
- 宽基节奏 → [index-long-term.md](references/index-long-term.md)
- 1R / 缺口 / 容量 → [execution-risk.md](references/execution-risk.md)
- 吸收的财务规则 → [absorbed-methods.md](references/absorbed-methods.md)
- 报告格式 → [report-template.md](references/report-template.md)
- 当时信息口径例子 → [worked-examples.md](references/worked-examples.md)

## 双模式采集

### 全量建档

分阶段写文件，阶段之间只交接文件，不靠聊天记忆：

1. `entity.json`：代码、交易所、法定名、曾用名、ADR/H/A 对应、子公司、品牌、高管、客户供应商别名
2. `ledger.jsonl`：证据账本，一行一条
3. `timeline.json`：过去 90 天事件与未来 6 个月催化剂
4. `market.json`：1 年日线与相对强弱；必要时 3 年周线
5. `redteam.md`：最强替代解释与是否被处理

每市场必查来源见 `information-collection.md`。硬清单打完即停，不要“再搜两轮”。

### 快速更新

只读已有档案，查新增公告、价格、催化剂和失效条件。档案超过保鲜窗口，退回全量建档。

## 证据账本

每行必填正好 10 个字段：

`claim_id, ticker, claim, value_unit, source_tier, url, retrieved_at, origin_cluster_id, polarity, evidence_grade`

可选：`timestamps`（发生 / 披露 / 获知 / 价格反应，或显式 missing）、`observation_window`、`decision_level`、`catalyst`、`exit_condition`。

A 级决策项缺少 S0–S2 URL、四时间戳、催化剂或退出条件 → 闸门失败。

来源等级：

- `S0` 法定记录：SEC / HKEX / 巨潮与交易所、法院、政府统计、成交数据
- `S1` 第一方：IR、业绩会、演示。只能证明公司怎么说
- `S2` 独立测量：客户供应商同行披露、政府或行业统计、合同招标许可
- `S3` 派生：行情、估值、一致预期、方法透明的另类数据
- `S4` 线索：新闻、论坛、社交。只用于发现，关键事实必须回到 S0–S2

两家聚合站引用同一份财报，只算一个 `origin_cluster_id`。FINRA short interest 与 daily short-sale volume 分开。13F / CCASS 必须标明滞后，不得写成实时资金流。

证据四档：`已验证事实 / 强证据 / 弱证据 / 传闻`
因果四档：`高度支持 / 条件支持 / 证据不足 / 不支持`

解释价格时拆成：背景脆弱性、直接触发、放大机制。同期新闻不是原因。

## 动作空间

无仓：`可执行` / `等待验证` / `不交易` / `不交易（组合约束）`
有仓：`继续持有` / `减仓` / `清仓` / `已触发退出`

`已触发退出` 立刻执行，不重新论证，不下移止损，不补仓，不改口长持。

宽基单独输出：`开始或继续分批配置` / `等待或再平衡` / `暂不配置` / `信息不足`
宽基默认配置，只调节奏。`暂不配置` 仅当估值、情绪、杠杆同时处历史极值，且红队用最高举证。不知现有美股占比 → `信息不足`。

## 可执行门槛

必须同时成立：

1. `evidence_gate.py` 输出 `PASS`
2. 净资产与持仓清单已知
3. 景气或基本面变量正在改善，盈利预期没有持续恶化
4. 价格与行业广度确认
5. 尚未充分定价的催化剂（见 `causal-evidence.md` 的代理指标）
6. 入场、失效位、目标、期限、时间止损齐全
7. `trade_math.py` 接受该计划：目标有独立依据，费用后盈亏比至少 `2:1`，组合与容量不超限

缺一则降为 `等待验证` 或 `不交易`。周期行业必须拆到子行业，检查盈利修正、订单、库存、价格、CapEx、估值扩张和拥挤度。过去上涨不是继续上涨的证据。

## 脚本

工作目录为本 Skill 根目录。

仓位与风控：

```bash
python scripts/trade_math.py < plan.json
```

证据闸门：

```bash
python scripts/evidence_gate.py ledger.jsonl
```

闸门退出码非 0 或 `status=FAIL` 时，禁止 `可执行`。
算术不要心算。脚本拒绝时把 `reason` 原文写入报告。

`trade_math.py` 硬限制：

- 多头计划风险默认 `≤1%` 净资产；空头 `≤0.5%`
- 仓位取 `min(计划风险, 缺口穿透后亏损≤2%)`
- 止损距离 `< 1.5 × ATR(20)` 拒绝
- 出清天数 `>1` 警告，`>3` 拒绝；空头参与率默认 `5%`，多头 `10%`
- 新仓后战术袖总开放风险 `>6%` 或单主题 `>2.5%` → `不交易（组合约束）`
- 宽基袖不计入主题热量，只作 beta 披露
- 目标依据必须是 `technical` / `valuation_reversion` / `event_reprice` 之一
- 无胜率只出费用后盈亏比，并写 `未校准估计`
- 空头无借券费、逼空偏高、杠杆工具 → 失败

## 红队

最终动作前，用只看账本的独立段落攻击主张。以下任一成立则禁止准出：

- 时序对不齐
- 同源转载被算成两源
- 最强替代解释未处理
- 没有退出计划
- “尚未充分定价”说不清对手方为什么在卖
- 闸门或交易脚本未跑

## 报告

严格按 [report-template.md](references/report-template.md) 输出。必须包含：日期、授权、净资产、持仓、袖、因果档、动作、脚本原文、未覆盖项、下次检查日。

“全部信息”= 所有可能改变当前动作的重大信息。列不出未覆盖项，就还没做完。

# 信息采集

“全部信息”= 所有可能改变当前动作的重大信息。列不出未覆盖项，采集就还没结束。

## 来源等级

- `S0` 法定记录：监管、交易所、审计财报、法院、政府统计、成交数据
  - 美股：SEC EDGAR 的 10-K、10-Q、8-K、20-F、6-K、DEF 14A、Form 4、13D/G（https://www.sec.gov/edgar/search/）
  - 港股：HKEXnews 公告、年报、通函、权益披露（https://www.hkexnews.hk/）
  - A 股：巨潮资讯与上交所 / 深交所公告
- `S1` 第一方：公司 IR、业绩会、演示、产品发布。只能证明公司如何表述
- `S2` 独立测量：客户、供应商、同行披露，政府或行业统计，合同、招标、许可
- `S3` 派生：行情、估值、一致预期、方法透明的另类数据
- `S4` 线索：新闻、论坛、社交。只用于发现，关键事实必须回到 S0–S2

Macrotrends、StockAnalysis、东方财富、AASTOCKS 等聚合站只用于检查抽取、单位和口径。两站引用同一份财报，`origin_cluster_id` 必须相同，只算一个源。法定披露才是 canonical。

## 硬清单：查完即停

不要“连续两轮补搜”。按市场把必查项打勾，打完就停。缺关键项就输出 `等待验证`，写清楚缺什么、去哪里补、预计何时更新。

### 美股个股

- [ ] 实体档案：代码、交易所、CIK、法定名、曾用名、ADR/H/A 对应、子公司、品牌、高管、客户供应商别名
- [ ] 最近 3 份 10-K / 20-F
- [ ] 最近 8 个 10-Q / 6-K 季度
- [ ] 最近 4 次业绩会纪要
- [ ] 最近 12 个月 8-K 与重大公告
- [ ] 最近 24 个月诉讼、融资、审计师变更、13D/G、Form 4
- [ ] 至少 24 个月行业供需、库存、价格、订单
- [ ] 最近 90 天事件时间线与未来 6 个月催化剂
- [ ] 1 年日线与相对行业 / 大盘；必要时 3 年周线
- [ ] 3–5 家同行，以及公开可识别的客户供应商
- [ ] FINRA short interest 与 daily short-sale volume 分列（https://www.finra.org/finra-data/browse-catalog/equity-short-interest）

### 港股 / A 股

同上，把法定来源换成 HKEX 或巨潮 / 交易所。港股加上权益披露与 CCASS（标明托管滞后）。A 股加上交易所异常波动公告。

### 宽基指数

- [ ] 指数编制与当前集中度
- [ ] 远期盈利与过去 10 年估值分位
- [ ] 实际利率、美元、信用利差
- [ ] 用户现有美股占比与定投节奏
- [ ] 再平衡日历

## 五路采集

1. 公司：财报、指引、电话会、管理层、资本配置、内部人、诉讼监管
2. 行业：供给、终端需求、库存、ASP、订单、取消率、产能利用率、CapEx、交期
3. 宏观：利率、汇率、流动性、商品、适用政策
4. 市场：价量、相对强弱、广度、估值、空头、公开资金
5. 另类：招聘、产品、应用排名、招标、专利、海关、舆情。只能加强假设，不能单独证明结论

## 账本 schema

JSONL，一行一个对象。必填正好 10 个字段：

```json
{
  "claim_id": "C001",
  "ticker": "AVGO",
  "claim": "FY25 Q2 revenue beat",
  "value_unit": "15000000000 USD revenue, company FY25 Q2 10-Q",
  "source_tier": "S0",
  "url": "https://www.sec.gov/...",
  "retrieved_at": "2026-09-03",
  "origin_cluster_id": "sec-10q-fy25q2",
  "polarity": "support",
  "evidence_grade": "verified"
}
```

可选：

```json
{
  "timestamps": {
    "occurred_at": "2026-06-01T00:00:00-04:00",
    "disclosed_at": "2026-06-05T16:05:00-04:00",
    "known_at": "2026-06-05T16:05:00-04:00",
    "priced_at": "2026-06-05T16:05:00-04:00"
  },
  "observation_window": "FY25 Q2",
  "decision_level": "A",
  "catalyst": "next earnings 2026-12-11",
  "exit_condition": "close below 20-day after failed hold"
}
```

规则：

- `source_tier` ∈ `S0 S1 S2 S3 S4`
- `polarity` ∈ `support oppose`
- `evidence_grade` ∈ `verified strong weak rumor`
- `decision_level` ∈ `A B C`，缺省 `B`
- A 级必须有 S0–S2 URL、四时间戳或显式 missing、以及账本中至少一条催化剂和一条退出
- 价格类 S3 的 `retrieved_at` 超过 1 个交易日 → 过期
- 同一 `origin_cluster_id` 去重后只计一个独立源

## 双模式落盘

全量建档目录建议：

```text
dossiers/<ticker>/
  entity.json
  ledger.jsonl
  timeline.json
  market.json
  redteam.md
```

快速更新只追加 `ledger.jsonl` 和改 `timeline.json`。阶段之间用文件交接，不靠对话记忆。

## 资金数据含义

| 数据 | 真实含义 | 禁止写法 |
|---|---|---|
| FINRA short interest | 双周或月度空头余额快照 | 今日空头流入 |
| FINRA short-sale volume | 当日以空头成交的量，不是新增空头余额 | 与 short interest 混用 |
| 13F | 季度末持仓，滞后约 45 天 | 实时机构流向 |
| CCASS | 托管变动，不是成交方向 | 北向实时买卖 |
| 期权成交 | 可能是对冲 | 散户看多/看空 |

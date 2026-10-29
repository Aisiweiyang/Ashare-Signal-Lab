# LABELING_GUIDE — 标注指南 v1.0（冻结）

> 标注对象：一条 `MarketText`（中文财经新闻/公告/研报摘要）。
> 每条文本由**两名标注者独立**标注，禁止互看；分歧进仲裁队列。LLM 预标注仅在工作台灰色展示，未经人工确认永不进入金标集。

## 0. 判定总原则

1. **只依据文本本身**，不使用标注者记忆中的后续市场走势或公司后续公告（防未来知识污染）。
2. 判断对象是文本中**主要目标证券/公司**（`target_security_id`；多目标时选主要目标并在理由中说明）。
3. 事实与观点都要标注情绪，但 `certainty` 反映文本自身的确定性语气。
4. 找不到证据支撑的标签一律 `unknown`，宁 unknown 勿猜。

## 1. 决策树

```
Q1 文本是否对某可识别目标表达评价/事实？
 ├─ 否 → direction=unknown, vs_expectation=unknown, certainty=unknown, 结束
 └─ 是 → Q2 文本主要语气对该目标是利多、利空还是中性事实陈述？
     ├─ 利多（收入/利润/订单/评级/政策利好等） → direction=bullish
     ├─ 利空（亏损/下滑/处罚/诉讼/减持等）     → direction=bearish
     └─ 纯客观陈述无可辨倾向                    → direction=neutral
Q3 文本是否提及与预期/前期/指引的比较？
 ├─ 超出预期/指引上限      → vs_expectation=above
 ├─ 落在预期区间/符合预期   → vs_expectation=inline
 ├─ 低于预期/指引下限      → vs_expectation=below
 └─ 未给出任何参照         → vs_expectation=no_expectation
Q4 文本的确定性语气？
 ├─ 已发生事实、审计数、定案公告 → certainty=certain
 ├─ 「预计/可能/有望/或」      → certainty=speculative
 ├─ 事实+条件（「若获批则…」）  → certainty=conditional
```

`unknown` 用于：文本残缺、指代不明、多目标冲突且无主、纯程序性公告（如定董事会议日期）。

## 2. 边界案例

- **反讽/质疑语气**（「业绩暴增，可信吗？」）：按字面方向标 `bullish`，`certainty=speculative`，理由注明反讽存疑；若明显看空质疑（「如此增速能否持续？**存疑**」且给出看空论据）→ `bearish`。
- **条件语句**：「若重组成功，利润翻倍」→ direction=bullish, certainty=conditional；条件主体未定 → `unknown`。
- **多目标**：主要目标按标题/首段主语；如「行业整体向好，A 公司或承压」→ 目标=A 公司，direction=bearish，certainty=speculative。
- **历史回顾**：纯历史事实无前瞻信息 → neutral + no_expectation + certain。
- **转载稿**：标注意见以**首发版本**为准（工作台显示首发链接）；本条仅标 `cluster` 归属提示。
- **预期参照缺失但有隐含比较**（「创历史新高」隐含超预期）：vs_expectation=above，理由必须引用证据跨度。

## 3. 证据跨度

每条标注至少 1 个 `evidence_span`（字符下标区间，工作台划选生成），指向支撑 `direction` 的最小文本片段。

## 4. 一致性与仲裁

- 指标：按维度的 Cohen's Kappa（direction / vs_expectation / certainty 各自计算；unknown 纳入 Kappa 计算）。
- Kappa < 0.6 → 停止扩量，回炉指南并修订版本（指南升级需 `guide_version` 递增 + 已标数据重估记录）。
- 仲裁者（角色 `adjudicator`）只能从两名标注者的标签中选择或给出第三标签，必须写理由；仲裁记录不可删除。

## 5. 导出格式

金标导出 JSON Lines，每行：`market_text_id, cluster_id, labels{direction,vs_expectation,certainty}, evidence_spans[], rationale, annotator_pseudonyms[], adjudicated(bool), guide_version, labeled_at`。无个人身份信息。

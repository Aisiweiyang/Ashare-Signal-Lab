# RESEARCH_PROTOCOL — 预注册式研究协议

版本：1.0（2026-09-07，编码完成前预注册；后续修订必须追加 Change Log，不得静默改写）
状态：`active`

## 1. 研究假设（预注册）

- **H1**：细粒度标签（方向 × 相对预期 × 确定性）构建的信号，在时间外测试集上的短期预测力显著优于三分类方向标签信号。判定统计量与阈值见 §5。
- **H0**：上述增量 ≤ 0。H0 不能被拒绝时，报告「细粒度维度无增量」，并继续完成系统与诊断。

探索性分析（不作为假设检验结论，须明确标注 exploratory）：分事件类型、分市场状态、衰减曲线、确定性维度的条件假阳性率。

## 2. 数据

- 演示与测试：**合成数据**（`asg_connectors.synthetic`），由固定 seed 生成，包含已知生成机制（信号注入强度可配置），用于方法验证与系统演示；不用于对外声明真实市场结论。
- 真实数据路径：仅接受用户提供或明确授权的公开文本（政府、交易所、上市公司公告）。任何 connector 必须声明允许用途与频率限制（见 DATA_GOVERNANCE.md）。
- 时间语义：`published_at`（发布）、`available_at`（市场可得，≥ published_at，考虑盘后/非交易时段推到下一可得时点）、`ingested_at`（入库）、`effective_at`（生效）。**严格回测仅允许 available_at ≤ 决策时点的数据。**

## 3. 标签体系（v1，冻结）

- `direction`: bullish / bearish / neutral / unknown
- `vs_expectation`: above / inline / below / no_expectation / unknown
- `certainty`: certain / speculative / conditional / unknown
- `target_type`: company / security / industry / index_macro / other
- `event_type`: earnings / orders / ma / regulatory / shareholder / product / litigation / macro_policy / other
- 辅助字段：`evidence_spans`（证据跨度，字符区间）、`rationale`（≤120 字理由）、`annotator_pseudonym`、`guide_version`、`quality_state`（pending/agreed/adjudicated/discarded）
- 决策树、边界案例、反讽与条件语句处理见 LABELING_GUIDE.md（v1.0 冻结后修订需升版本号并记录 AgreementRun 重估）。

## 4. 数据集与切分

- 金标集：双人独立标注一致（或仲裁后）的样本；LLM 预标注仅作辅助展示，**未人工确认不得进入金标集**。
- 切分：**walk-forward 时间切分**（train 早于 test，按 `available_at` 日期），禁用随机打乱切分；窗口间 embargo ≥ 2 个交易日；同事件转载簇（TextCluster）不得跨集合泄漏（以 cluster_id 为单位整体归属）。
- 切分定义持久化为 `SplitDefinition`（含名单哈希），实验只能引用已存在的切分。

## 5. 评价与判定

- 主指标：Spearman 秩相关 IC（信号 vs 事件后 T+0→T+1 收益），及方向命中率。
- 判定 H1 成立需同时满足：
  1. 细粒度信号 IC − 三分类信号 IC 的 bootstrap 95% CI 下界 > 0；
  2. 时间外样本事件数 ≥ 200（合成演示数据上若不足，结论标注为「方法验证」而非「市场结论」）；
  3. 在 ≥ 2 个时间窗（walk-forward 折）上方向一致。
- 多重比较：预注册之外的变体探索须用 Bonferroni 或报告为 exploratory；禁止只报告最佳实验。
- 基线必须报告：多数类、中文金融词典阈值、传统三分类（sklearn TF-IDF+LogReg）、细粒度模型。LLM 与小模型结果与人工金标**分开**评价。

## 6. 事件研究与回测规范

- 收益口径：复权后收盘价；事件窗 T+0 收盘 → T+1 / T+2 收盘；异常收益 = 个股收益 − 市场基准（等权指数）收益。
- 可成交性：涨停不得买入、跌停不得卖出、停牌不可交易、T+1 约束当日买入不可卖出；缺失行情 → 不可成交。
- 成本：佣金（默认万 2.5，双边）+ 印花税（卖出千 0.5，按 2023-08 减半后口径，可配置）+ 滑点（默认 10bp，敏感性分析 5/10/20/50bp）。
- 指标：IC、ICIR、命中率、平均异常收益、Sharpe（日频年化）、最大回撤、换手率、盈亏比；全部报告置信区间与样本量。
- 衰减分析：信号滞后 1/2/3/5 日后的 IC 衰减曲线。

## 7. 反泄漏红线（自动化测试强制）

1. 注入「未来公告/未来价格」必须导致泄漏测试失败（测试故意断言抛错）。
2. `published_at` 缺失或模糊的文本不得进入严格回测（`strict` 模式抛 `AvailabilityError`）。
3. 跨集合重复文本（同 cluster）必须被切分器拒绝。
4. 任何指标计算函数接收「未来窗口行情」时必须按 available_at 拒绝。
5. 随机切分接口存在但必须显式标注 `allow_future_leak=True` 才能调用，且结果标记为不可用于结论。

## 8. 审计与可复现

每次实验运行记录：`input_dataset_hash`、`code_commit`、`config_hash`、`model_version`、`prompt_version`、`random_seed`、`started_at`、`finished_at`、`environment`（Python/OS/包版本）。结果不可复现视为失败。负面结果（不显著的实验）与正面结果同等保留和展示。

## Change Log

- v1.0 (2026-09-07)：初始预注册。

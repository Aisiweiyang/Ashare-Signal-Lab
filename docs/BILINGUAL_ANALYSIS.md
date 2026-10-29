# 中英文财经文本分析

入口是工作台“双语情绪分析”，或`asg_models.analyze_bilingual`。证据坐标属于返回的`normalized_text`，不能直接定位原始HTML/PDF。目标名称与别名由调用方提供，未自动认证证券身份。

## 支持范围

- HTML实体、NFKC、引号、负号与段落规范化；保留金额和比例。
- 中文短语和英文词边界；最长不重叠证据；常见英文动词形式与缩约否定。
- 条件、计划、推测；有限日期上下文的May处理。
- 亏损收窄/扩大、盈亏转换；成本与利润的方向区别。
- 有限同句同指标实际/预期比较：货币量级、百分比、百分点、基点；数值水平与变化量分别处理。
- 中英文句末统一终止条件、目标与数值作用域；小数和有限缩写得到保护。跨句隐含主体不自动绑定，部分未归属财经句会提示复核。

中文需确定词界，英文还需处理词形和缩约词。例如`losses fell`描述亏损减少；`hasn't been approved`否定获批。`5%`与`5个百分点`不是同一口径，`50 basis points`可归一为0.5个百分点。`fell to`后的金额水平不能因为fell就取负。

## 响应与接口

`labels.direction`是规则概括的金融倾向；`vs_expectation`描述比较关系；`certainty`是文字语气。`flags`和分句证据应一起检查，成本低于预期的方向可能与利润低于预期相反。规则强度既不是校准概率，也不是收益率。

接口均要求登录及`texts.read`权限：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/analysis/text` | text、title及可选目标/别名/其他公司 |
| POST | `/api/analysis/batch` | 最多32条，标题正文合计64000字符 |
| GET | `/api/analysis/market-text/{id}` | 库内文本及证券归属 |

正文上限18000字符、标题1000字符。分析不修改原文、人工标签、事件或账户。规则依据来自当前文字，不会主动调用外部服务。

## 阅读与复跑

1. `packages/text_pipeline/src/asg_text_pipeline/bilingual.py`：规范化与语言提示。
2. `packages/models/src/asg_models/bilingual.py`：句界、目标、否定、比较和证据。
3. `packages/models/src/asg_models/bilingual_baseline.py`：可训练特征。
4. `apps/api/src/asg_api/routers/analysis.py`：输入约束与权限。
5. `apps/web/src/pages/Analysis.tsx`：输入、分句、证据与结果。

运行`uv run python scripts/evaluate_bilingual.py`和`uv run python scripts/evaluate_language_contrast.py --output output/language_contrast.json`。开发集输出写入忽略目录，不随仓库分发。每条输入、预期字段、实际标签及证据都可检查；这些集合已参与开发，不是独立真实验证集。

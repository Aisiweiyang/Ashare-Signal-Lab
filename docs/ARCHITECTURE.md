# 架构与数据流

本公开快照为本地模块化单体：React/TypeScript工作台调用FastAPI，API在进程内使用研究包，默认存储为SQLite与本地文件。

| 包 | 职责 |
|---|---|
| asg_domain | ORM、枚举、时间/日历、质量契约、版本绑定 |
| asg_connectors | 合成数据、CSV、可选行情连接器及注册表 |
| asg_text_pipeline | 规范化、实体归属、文本与事件去重 |
| asg_labeling | 独立标注、一致性、仲裁与金标 |
| asg_models | 词典、TF-IDF、模型provider、独立双语规则 |
| asg_event_study | 事件收益、IC、异常收益与统计 |
| asg_backtest | 模拟成交、费用、时间切分 |
| asg_pipeline | 导入、演示生成与研究编排 |

## 两条文本路径

传统实验使用CSV/合成连接器→清洗和聚类→可得时间对齐→标注与provider预测→信号→事件研究和回测。API提供权限控制与审计，实验记录输入和配置身份。旧清洗路径和模型语义保留，不能把其结果直接视为新双语模型结果。

交互双语分析使用原始输入或库内清洗文本→专用规范化→句界与目标→否定/语气/数值比较→标签、证据、复核标记。此路径不会自动写回原文、人工金标、事件或组合。证据坐标属于规范化文本；库内输入已经过旧清洗，仍应回查原始文档。

## 数据边界

启动器默认使用独立演示库，数据库seed及生成器版本不兼容时拒绝混用。测试使用临时库，回归基准位于tests/integration/baselines。手工开发用例位于research/fixtures；运行输出写入被Git忽略的output或research/runs。

LLM provider可选；默认mock不产生外部请求。akshare是单独extra，不是演示启动依赖；安装客户端不代表上游数据授权或可用性已获验证。完整PostgreSQL/对象存储部署未在本公开快照验收，容器模板未分发。

核心来源位置、API和限制见[使用说明](USAGE.md)、[双语分析](BILINGUAL_ANALYSIS.md)、[API](api_contract.md)与[模型卡](MODEL_CARD.md)。

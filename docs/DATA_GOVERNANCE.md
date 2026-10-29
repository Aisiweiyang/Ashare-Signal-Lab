# DATA_GOVERNANCE — 数据治理

## 数据分类

| 类别 | 内容 | 存储位置 | 可否出 Git |
|---|---|---|---|
| 合成演示数据 | `asg_connectors.synthetic` 固定 seed 生成 | `data/`（可重建） | 否（可复现重建） |
| 用户提供文本 | 用户授权导入的公告/新闻 | `data/` + 原始层（只追加） | **否** |
| 行情数据 | fixture 或授权来源 | `data/` + Parquet | 否 |
| 标注数据 | 匿名化后金标 | DB + 导出 Parquet | 导出物默认本地 |
| 密钥/Cookie | `.env`（gitignore） | 本机 | **禁止提交** |

## 时间语义（强约束）

- `published_at`：来源声明发布时间；缺失或只有日期无时刻 → `available_at` 保守推到该日 24:00 后的下一可交易时点。
- `available_at`：市场参与者实际可读到的时间。15:00 后发布 → 次日开盘前才可得（用于 T+1 决策）。
- `ingested_at`：本系统入库时间，仅审计用，不参与特征。
- `effective_at`：事件生效时间（如公告决议日）。
- 任何进入严格回测的特征必须满足 `available_at <= decision_time`；违反即 `AvailabilityError`（有自动化测试）。

## 原始层只追加

`SourceDocument`/`SourceSnapshot` 不可修改；修正通过新版本行（`version` 递增 + `supersedes_id`）完成。删除仅限合规要求（保留删除审计记录）。

## 最小化与匿名化

- 标注者仅存 `annotator_pseudonym`（如 `annotator_A`）；真实身份映射若存在只存本地 `data/annotator_map.json`（gitignore），任何导出前剥离。
- 不采集不必要的个人信息；文本中的身份证号/手机号在清洗阶段脱敏（`asg_text_pipeline.anonymize`）。

## 质量 contract（workers 与 tests 共用）

`asg_domain.quality` 定义断言：schema、主键唯一、引用完整性、行情连续性（交易日无缺口）、时区（全部东八区显式）、发布时间不晚于入库、重复事件簇一致、标签值域、未来时间拒绝。数据质量页展示每次运行的断言结果。

## 保留与删除

- 演示环境：`scripts/reset_demo.ps1` 一键清除重建。
- 用户数据：提供按 `source_id`/日期范围的删除 API（admin 角色），删除写入审计日志；备份含删除记录。
- 备份：`scripts/backup.ps1` 导出 DB 快照 + Parquet 校验和；恢复演练记录见 TEST_REPORT §恢复演练。

## 合规红线

禁止：绕过登录/验证码/付费墙/反爬；批量导出商业数据库；抓取个人敏感信息；把第三方原文或模型输出当可自由再分发数据发布。演示默认只用合成数据。

# API Contract — AshareSignal Lab

Base URL：`http://127.0.0.1:8300`。全部路由前缀 `/api`。
认证：`Authorization: Bearer <token>`（`POST /api/auth/login` 获取）。
OpenAPI 文档：启动后访问 `/docs`（Swagger UI，由 FastAPI 自动生成）。

限流：全路由 120 req/min/IP（`ASG_RATE_LIMIT_PER_MINUTE` 可调），超限 429。
响应：JSON；错误统一 `{"detail": "..."}`；安全头（CSP/nosniff/DENY）全响应注入。

## 认证

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/auth/login` | 公开 | `{username,password}` → `{access_token,role,display_name}`；5 次失败锁 5 分钟（423） |
| GET | `/api/auth/me` | 登录 | 当前用户信息 |
| GET | `/api/health` | 公开 | 存活检查（含 research_only / real_trading_disabled 标志） |

## 文本

| 方法 | 路径 | 权限 | 参数/说明 |
|---|---|---|---|
| GET | `/api/texts` | viewer+ | `page,page_size≤100,security_symbol,strict_only`；分页 `{total,items}` |
| GET | `/api/texts/{id}` | viewer+ | 详情（cleaned_text、available_at、cluster_id、quality_flags） |

## 标注

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/labeling/tasks` | viewer+ | 任务列表 |
| POST | `/api/labeling/tasks` | researcher+ | `{name, market_text_ids≤2000}` 建任务 |
| GET | `/api/labeling/tasks/{id}/items` | viewer+ | 条目 + 我的标注 + LLM 预览（仅展示） |
| POST | `/api/labeling/annotations` | annotator+ | 三维标签 + 证据跨度 + rationale；`is_preview=true` 永不进金标 |
| POST | `/api/labeling/tasks/{id}/agreement` | researcher+ | 计算 Cohen's Kappa（按维度） |
| POST | `/api/labeling/adjudications` | adjudicator+ | 仲裁分歧（只追加，仲裁后标注不可改） |
| GET | `/api/labeling/tasks/{id}/gold` | researcher+ | 金标集（agreed+adjudicated） |
| GET | `/api/labeling/tasks/{id}/disagreements` | adjudicator+ | 仲裁工作队列 |

标签值域（强校验，非法返回 400）：`direction∈{bullish,bearish,neutral,unknown}`；`vs_expectation∈{above,inline,below,no_expectation,unknown}`；`certainty∈{certain,speculative,conditional,unknown}`。

## 实验 / 回测

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/experiments` | viewer+ | 实验 + 回测运行列表 |
| POST | `/api/experiments/run` | researcher+ | `{name,seed,n_events,n_days,provider,folds,slippage_bps}` → 完整 manifest（含 H1 判定）；失败 400 + 审计 |
| GET | `/api/experiments/{run_id}` | viewer+ | 详情（config/seed/dataset_hash/metrics/manifest） |
| GET | `/api/experiments/backtests/{run_id}/metrics` | viewer+ | 回测指标明细 |

## 数据

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/data/securities` | viewer+ | 证券列表 |
| GET | `/api/data/price-bars/{security_id}` | viewer+ | `limit≤1000` 日行情（含涨跌停/停牌标记） |
| POST | `/api/data/import/csv` | researcher+ | multipart 上传；UTF-8、≤5MB、列白名单；行级失败隔离（`row_errors`） |
| POST | `/api/data/seed-demo` | researcher+ | `?seed=&n_events=` 播种合成演示数据 |
| POST | `/api/data/generate-bars` | researcher+ | 为库内证券补合成行情 |
| GET | `/api/data/quality` | viewer+ | 数据质量契约断言结果 |

## 管理 / 导出

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET/POST | `/api/admin/users` | admin | 用户管理（角色枚举校验、用户名 pattern、密码 ≥8） |
| GET | `/api/admin/audit` | admin | 审计日志（只追加，无删除接口） |
| GET | `/api/export/gold/{task_id}` | researcher+ | 金标 JSONL（匿名化） |
| GET | `/api/export/entities` | researcher+ | 跨项目 contract：`entity_id/security_id/event_id/source_id`（v1.0） |

## 错误码约定

401 未认证 / 403 越权（RBAC 能力表拒绝）/ 404 不存在 / 422 参数校验失败 / 423 账户锁定 / 429 限流 / 400 业务规则失败（标签值域、样本不足、上传非法等）/ 500 内部错误（不泄露栈细节）。

## 双语分析

需要`texts.read`权限：`POST /api/analysis/text`、`POST /api/analysis/batch`、`GET /api/analysis/market-text/{id}`。输入上限、目标与证据坐标见[BILINGUAL_ANALYSIS](BILINGUAL_ANALYSIS.md)。

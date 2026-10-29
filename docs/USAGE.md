# 使用说明

从仓库根目录启动 `scripts/demo.ps1`（Windows）或 `scripts/demo.sh`（Linux/macOS）。安装、账号和测试命令见[README](../README.md)。

## 工作台流程

1. 登录后在“双语情绪分析”粘贴中文、英文或混合财经文字。填写目标、别名和其他公司可以约束归属，但不自动验证证券身份。
2. “文本浏览”查看库内原文、清洗文本、时间与质量信息，也可分析当前记录。
3. “标注工作台”由不同标注者独立填写三个标签与证据；研究员计算一致性，仲裁员处理分歧。
4. “实验中心”运行合成研究，检查标签、时间对齐、模型和模拟账户结果。模型输出和人工金标分别评价。
5. “数据质量”和“审计”用于检查缺口与操作记录。导出接口需要相应权限。

本地演示不自动调用收费模型或远程行情。自有文本可使用CSV入口导入；真实授权和来源时间必须自行提供，未知保持未知。复制 `.env.example` 为 `.env` 时只在本地填写配置，禁止提交真实密钥。

## 启动控制

- Windows：`-FreshDemo`创建新演示库；`-SkipSync`仅适用于已正确安装的环境；`-NoServe`只准备环境；`-Port 8301`改变端口。
- Linux/macOS：`--fresh-demo`、`--skip-sync`、`--no-serve`。
- 默认演示库是`data/demo/ashare_lab.db`。显式设置`ASG_DATABASE_URL`时程序检查数据库绑定，不向不兼容研究库播种。

## 常见问题

- `ModuleNotFoundError`：在当前目录重新执行`uv sync --frozen --group e2e --extra market`，不要复制其他目录的`.venv`。
- 无法构建网页：确认Node/npm已安装，进入`apps/web`执行`npm ci`与`npm run build`。
- 数据绑定不匹配：保留原库，用新演示库；不要删除绑定或覆盖研究数据。
- 浏览器测试跳过：安装Playwright及Chromium，再重新测试；不能把跳过记为已验收。
- Windows临时目录或BLAS资源异常：设置`OPENBLAS_NUM_THREADS=1`，并使用项目内`--basetemp`。

默认账号、内存限流和单机SQLite适合本地研究演示；公共网络部署需要另行审查认证、密钥、存储和运维配置。本开源快照不宣称生产部署验收。

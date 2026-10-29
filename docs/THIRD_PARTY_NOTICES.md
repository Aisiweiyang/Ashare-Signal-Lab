# 第三方声明

本仓库分发本项目源码及自建合成用例，不分发依赖安装目录、模型权重、第三方数据集或历史下载文件。项目源码许可证见[MIT](../LICENSE)；依赖各自保留其授权条件。

Python版本固定于`.python-version`，依赖解析固定于`uv.lock`；前端依赖固定于`apps/web/package-lock.json`。主要组件包括FastAPI/Starlette、Uvicorn、Pydantic、SQLAlchemy、Alembic、NumPy、SciPy、scikit-learn、jieba、PyYAML、HTTPX，以及React、ECharts、Vite和TypeScript。

这些依赖主要采用MIT、BSD或Apache-2.0等许可证；具体版本及完整传递组件应以锁文件和实际安装包的许可证文件为准。项目MIT授权不替代第三方授权，也不授予外部网站数据的抓取或再分发权利。

可选akshare只提供客户端适配；默认关闭远程抓取。使用前须核对数据源授权、条款、频率与来源。演示文字和行情由本项目合成，真实数据不随仓库分发。内置交易日历是近似fixture。

报告依赖问题时提供包版本、锁文件及复现步骤，勿上传访问令牌或私人文本。

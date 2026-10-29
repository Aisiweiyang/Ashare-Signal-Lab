# Ashare-Signal-Lab · A股信号实验室

**作者：[Aisiweiyang](https://github.com/Aisiweiyang)**。项目源码与自建用例版权归作者所有，按[MIT许可证](LICENSE)开放使用；第三方组件保留各自版权及许可。作者信息见[AUTHORS.md](AUTHORS.md)。

面向中文、英文和混合财经文本的情绪分析与可复现研究工具。提供本地工作台、文本导入、标注仲裁、模型基线、事件研究和合成行情回测。

**这是独立项目。** 默认数据是虚构公司、合成文字和合成行情；真实文本准确率、真实市场增量与收益效果尚未验证。不连接券商，不执行真实交易。

## 能做什么

- **双语分析**：检查目标公司、否定、条件、预期差和有限的财务数字比较，返回分句、证据及复核标记。
- **数据与标注**：CSV文本导入、去重聚类、证券归属、时间对齐、双人标注、一致性与仲裁、金标导出。
- **研究流程**：词典、TF-IDF和可配置模型接口；时间切分、泄漏检查、事件研究、带模型费用的模拟回测。
- **可检查结果**：输入、配置、代码身份和运行记录；未知信息保留为未知。

交互双语规则版本为 `bilingual_finance_rules_v2.1`。传统实验使用另一条历史模型路径，两者不自动互换；详见[模型卡](docs/MODEL_CARD.md)。规则强度不是正确概率或股价收益预测。

## 本地启动

需要 Python 3.13、uv、Node.js 24（含npm）。先安装这些工具，再执行：

```powershell
git clone https://github.com/Aisiweiyang/Ashare-Signal-Lab.git
cd Ashare-Signal-Lab
powershell -ExecutionPolicy Bypass -File scripts/demo.ps1
```

Linux/macOS：在仓库目录执行 `bash scripts/demo.sh`。本次已验证平台见[验收记录](docs/VALIDATION.md)。

打开 <http://127.0.0.1:8300>。演示账号：`researcher`（研究）、`annotator_a`/`annotator_b`（标注）、`adjudicator`（仲裁）、`viewer`（查看）、`admin`（管理）；演示密码均为 `Demo12345!`。这些是本地演示账号，服务默认只监听回环地址。

启动器首次生成独立演示库，后续复用；不会删除已有研究库。Windows使用 `-FreshDemo`、Linux/macOS使用 `--fresh-demo` 创建另一个演示库。目录迁移后应重新同步依赖，不要直接复用旧目录的虚拟环境。

## 双语分析示例

登录后进入“双语情绪分析”，输入文字并按需填写目标公司和别名。例如：

> Company profit fell 10% versus expected decline of 20%.

分析会区分文本线索与明确预期比较；数值期间或单位不一致时保留复核标记。结果代表有限规则的解释，不保证正确。复杂否定、跨段指代、通用财报表格和所有会计口径尚未覆盖。

Python调用无需启动网页：

```python
from asg_models import analyze_bilingual
result = analyze_bilingual("Company losses fell.")
print(result["labels"], result["flags"])
```

## 开发与验证

```powershell
uv sync --frozen --group e2e --extra market
uv run python -m playwright install chromium
cd apps/web
npm ci
npm run build
cd ../..
uv run ruff check packages apps/api/src tests scripts
uv run pytest tests -q -n 2 --basetemp=.pytest_tmp/tests
uv run python scripts/evaluate_language_contrast.py --output output/language_contrast.json
```

Windows测试建议设置 `OPENBLAS_NUM_THREADS=1`。浏览器和依赖需先安装；测试通过、真实语料效果和市场收益是不同的验收事项。

## 目录

| 目录 | 内容 |
|---|---|
| `apps/api` / `apps/web` | FastAPI与React工作台 |
| `packages` | 数据领域、连接器、清洗、标注、模型、事件研究与回测 |
| `tests` | 单元、集成、防泄漏、权限和浏览器测试 |
| `research/configs` / `research/fixtures` | 研究配置及手工开发用例 |
| `scripts` / `workers` | 启动、验证与实验入口 |

仓库不包含数据库、真实用户数据、密钥、历史运行输出、依赖缓存、Word/PDF手册或私人项目历史。仅保留回归测试所需的合成基准；数据在运行时生成。未经验证的容器部署模板和一次性文档/历史基准生成脚本不在此开源快照内。

文档：[使用说明](docs/USAGE.md) · [双语分析](docs/BILINGUAL_ANALYSIS.md) · [架构](docs/ARCHITECTURE.md) · [数据卡](docs/DATA_CARD.md) · [模型卡](docs/MODEL_CARD.md) · [研究协议](docs/RESEARCH_PROTOCOL.md) · [API](docs/api_contract.md) · [验收](docs/VALIDATION.md)

## 许可证

本项目源码与自建合成用例采用 [MIT License](LICENSE)。第三方依赖及用户导入数据分别遵循其许可证和使用条件，见[第三方声明](docs/THIRD_PARTY_NOTICES.md)。后续更新以仓库实际提交为准。

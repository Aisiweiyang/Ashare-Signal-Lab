"""领域错误类型。前视偏差相关错误必须显式抛出，禁止静默降级。"""

from __future__ import annotations


class AshareSignalError(Exception):
    """项目错误基类。"""


class AvailabilityError(AshareSignalError):
    """数据在决策时点不可得（available_at 晚于决策时点 / 发布时间缺失）。

    这是反前视偏差的核心防线：严格回测与特征构建遇到该错误必须向上传播，
    不得捕获后继续计算。
    """


class CalendarError(AshareSignalError):
    """交易日历无法解析给定日期/缺级行情。"""


class LabelValidationError(AshareSignalError):
    """标签值域/组合非法（如 quality_state=adjudicated 但缺少仲裁记录）。"""


class QuarantinedOutputError(AshareSignalError):
    """模型/LLM 输出未通过 JSON Schema 校验，进入隔离队列。"""


class BudgetExceededError(AshareSignalError):
    """LLM 调用预算超限（kill switch 语义，必须中止批次）。"""


class TradingDisabledError(AshareSignalError):
    """任何试图启用真实交易的路径都应抛出该错误（系统定位禁止真实交易）。"""


class ConnectorError(AshareSignalError):
    """数据源连接器层错误（停用、限流、上游不可达等）。"""


class ConnectorUnavailableError(ConnectorError):
    """连接器不可用：可选依赖未安装、显式停用或上游数据不可得。

    可选 connector（如 akshare）以此错误向调用方如实声明「不可用」，
    由调用方决定降级路径；connector 自身不得静默替换数据源。
    """


class BindingError(AshareSignalError):
    """数据库版本绑定校验失败（ADR-014）。

    实验/写入请求与库内 `DatabaseBinding` 记录冲突（seed 或生成器版本不匹配）、
    或未绑定遗留库未显式豁免时抛出——把 ADR-012 的「跨 seed 必须全新库」运行
    纪律从事后核验（n_new_bars）提升为打开时的机器强制。
    """

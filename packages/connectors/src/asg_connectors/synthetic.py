"""确定性合成数据生成器（演示与测试专用）。

生成机制（写明以便审计，详见 docs/DATA_CARD.md）：
- 8 家**虚构**公司、180 个交易日（2026-01-05 起，使用内置近似日历）；
- 事件文本由模板库生成，携带潜在标签（direction/vs_expectation/certainty/event_type）；
- 潜在标签 → 价格反应注入（见 market.py），强度可配置——这允许 H1 在「标签可恢复」
  的前提下被诚实检验，而不是预设结论；
- 25% 事件带 1-2 条转载（前缀改写，published_at 更晚）用于去重聚类；
- 15% 噪声文本（程序性公告/行业宏观/残缺文本）标签为 unknown；
- 约 10% 文本只有日期精度（无时刻），严格模式下必须被拒之严格回测之外。

v2（2026-09-12，金标扩量）：① 每条信号文本追加 2 条**标签中性**公告样板句
（_FLAVOR_SENTENCES，经词典零命中测试约束）。起因：扩容实验中发现同模板+同公司、
仅数字不同的「近重复」文本在 SimHash（Hamming≤6）下被大量误并同簇（ADR-009 已知
限制的量化证据，见 research/runs/simhash_scale_*.json）；真实公告本就带有此类格式化
结尾，追加样板句使不同事件文本可区分，转载（前缀+原文全文复制）仍正常聚簇。
② 补齐 (bearish,above)/(neutral,above)/(neutral,below) 三组模板缺口——v1 加权分布
会抽到这些无模板组合并静默丢弃事件（约 23%）。
`generate_universe(diversify_flavor=False)` 不追加样板句、不消耗额外随机数（文本形态
同 v1）；但同 seed 下事件序列与历史版本不完全一致（组合覆盖变全），可复现性以
manifest 记录的代码 commit 为准。

一切由 seed 决定：同 seed 逐字节可复现。
所有公司名称均为虚构；若证券代码与真实代码雷同纯属巧合，不指向任何真实公司。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

import numpy as np
from asg_domain.calendar import TradingCalendar
from asg_domain.enums import Certainty, Direction, EventType, VsExpectation

from asg_connectors.base import ConnectorMeta, RawDoc

SYNTHETIC_SOURCE_ID = "synthetic_demo_v1"
GENERATOR_VERSION = "synthetic_demo_v2_flavor"  # v1=纯模板；v2=追加标签中性样板句（见模块 docstring）

# (symbol, exchange, short_name, industry) —— 全部虚构
SYNTHETIC_SECURITIES: list[tuple[str, str, str, str]] = [
    ("601907", "SSE", "云舟数控", "装备制造"),
    ("600382", "SSE", "岚台生物", "生物医药"),
    ("002481", "SZSE", "沧澜化工", "基础化工"),
    ("003619", "SZSE", "禾光农业", "农林牧渔"),
    ("300759", "SZSE", "玖州传媒", "传媒娱乐"),
    ("300880", "SZSE", "沛然电子", "电子元件"),
    ("601519", "SSE", "谷雨软件", "软件开发"),
    ("600760", "SSE", "昙桥能源", "采掘服务"),
]


@dataclass
class LatentLabels:
    """生成器内部的潜在真值；仅供模拟标注者与诊断，不进入 MarketText。"""

    direction: Direction
    vs_expectation: VsExpectation
    certainty: Certainty
    event_type: EventType


@dataclass
class SyntheticEvent:
    event_seq: int
    symbol: str
    published_at: _dt.datetime
    published_precision: str
    text: str
    title: str
    latent: LatentLabels
    is_repost: bool = False
    repost_of_seq: int | None = None


# ---------------------------------------------------------------- 模板库
# 每个条目: (direction, vs_expectation, certainty, event_type, 标题模板, 正文模板)

_T: list[tuple[Direction, VsExpectation, Certainty, EventType, str, str]] = []


def _t(d, v, c, e, title, body):
    _T.append((d, v, c, e, title, body))


# —— 看涨 ——
_t(Direction.BULLISH, VsExpectation.ABOVE, Certainty.CERTAIN, EventType.EARNINGS,
   "{name}业绩快报：净利润同比大增{pcta}%",
   "{name}（{symbol}）发布业绩快报，报告期实现营业收入{rev}亿元，同比增长{pcta}%；归母净利润{profit}亿元，同比增长{pctb}%，超出市场一致预期。公司称业绩增长主要来自主营业务放量。")
_t(Direction.BULLISH, VsExpectation.ABOVE, Certainty.CERTAIN, EventType.ORDERS,
   "{name}签订重大合同，金额{rev}亿元",
   "{name}（{symbol}）公告与某大型客户签署销售合同，合同金额{rev}亿元，占上年营业收入的{pcta}%，超出此前市场预期的{pctb}%规模。合同已双方盖章生效。")
_t(Direction.BULLISH, VsExpectation.ABOVE, Certainty.CERTAIN, EventType.MA,
   "{name}完成收购交易，并表增厚业绩",
   "{name}（{symbol}）公告已完成对某标的公司股权收购交割，持股{pcta}%。据业绩承诺，标的今年净利润不低于{profit}亿元，高于此前方案中的承诺值。")
_t(Direction.BULLISH, VsExpectation.INLINE, Certainty.CERTAIN, EventType.EARNINGS,
   "{name}半年报：净利润同比增长{pcta}%，符合预期",
   "{name}（{symbol}）披露半年报，营业收入{rev}亿元，同比增长{pcta}%；归母净利润{profit}亿元，同比增长{pctb}%，落在券商一致预期区间内。")
_t(Direction.BULLISH, VsExpectation.INLINE, Certainty.CERTAIN, EventType.PRODUCT,
   "{name}新产品获得上市许可",
   "{name}（{symbol}）公告其新产品已取得监管部门上市许可，公司表示将按既定计划于下季度投产，产销安排与此前经营计划一致。")
_t(Direction.BULLISH, VsExpectation.NO_EXPECTATION, Certainty.CERTAIN, EventType.SHAREHOLDER,
   "{name}控股股东完成增持{pcta}%股份",
   "{name}（{symbol}）公告控股股东本次增持计划已实施完成，累计增持{pcta}%股份，耗资{rev}亿元。")
_t(Direction.BULLISH, VsExpectation.ABOVE, Certainty.SPECULATIVE, EventType.ORDERS,
   "传{name}斩获海外大额订单，供货规模或翻倍",
   "有市场消息称{name}（{symbol}）正在洽谈一笔海外订单，金额或达{rev}亿元，若属实其海外收入占比有望明显提升。公司未正式回应，尚待公告确认。")
_t(Direction.BULLISH, VsExpectation.ABOVE, Certainty.SPECULATIVE, EventType.EARNINGS,
   "{name}全年业绩有望超预期？券商上调预测",
   "多家券商研报认为{name}（{symbol}）四季度出货好于此前指引，全年净利润可能超出一致预期{pcta}%左右。上述判断基于渠道调研，存在不确定性。")
_t(Direction.BULLISH, VsExpectation.ABOVE, Certainty.CONDITIONAL, EventType.PRODUCT,
   "{name}新产线若获认证通过，明年出货或增{pcta}%",
   "{name}（{symbol}）在建新产线已进入认证流程。公司提示，若最终通过下游大客户认证，预计新增年产能对应收入{rev}亿元；认证结果与时间存在不确定性。")
_t(Direction.BULLISH, VsExpectation.NO_EXPECTATION, Certainty.SPECULATIVE, EventType.MACRO_POLICY,
   "机构预计行业补贴政策或于近期落地，利好{name}",
   "有分析认为，若新一轮设备更新补贴于本月落地，{name}（{symbol}）作为头部厂商有望受益。政策细则与力度尚不明确。")
_t(Direction.BULLISH, VsExpectation.BELOW, Certainty.CERTAIN, EventType.EARNINGS,
   "{name}业绩预告：净利同比增长{pcta}%，但低于市场预期",
   "{name}（{symbol}）发布业绩预告，预计净利润{profit}亿元，同比增长{pcta}%。不过该增速低于此前市场一致预期的{pctb}%，券商此前普遍预测更乐观。")
_t(Direction.BULLISH, VsExpectation.INLINE, Certainty.SPECULATIVE, EventType.REGULATORY,
   "{name}牌照续期审查推进中，通过概率较大",
   "接近监管人士称，{name}（{symbol}）的经营牌照续期审查进展顺利，有望按期获得续期。最终以监管公告为准。")

# —— 看跌 ——
_t(Direction.BEARISH, VsExpectation.BELOW, Certainty.CERTAIN, EventType.EARNINGS,
   "{name}业绩快报：净利润同比下滑{pcta}%，低于预期",
   "{name}（{symbol}）发布业绩快报，报告期营业收入{rev}亿元，同比下降{pcta}%；归母净利润{profit}亿元，同比下滑{pctb}%，明显低于市场一致预期。公司解释主因产品价格下行。")
_t(Direction.BEARISH, VsExpectation.BELOW, Certainty.CERTAIN, EventType.REGULATORY,
   "{name}收到监管部门行政处罚，罚款{rev}万元",
   "{name}（{symbol}）公告收到监管局《行政处罚决定书》，因信息披露违规被处以罚款{rev}万元，并责令改正。处罚决定已生效。")
_t(Direction.BEARISH, VsExpectation.BELOW, Certainty.CERTAIN, EventType.LITIGATION,
   "{name}重大诉讼一审判决败诉，计提预计负债{rev}万元",
   "{name}（{symbol}）公告收到一审判决书，法院判令公司赔偿原告{rev}万元。公司已按判决计提预计负债，该判决为一审判决，公司不再上诉。")
_t(Direction.BEARISH, VsExpectation.BELOW, Certainty.CERTAIN, EventType.ORDERS,
   "{name}公告主要客户取消采购订单",
   "{name}（{symbol}）公告，某主要客户通知取消原定采购订单，涉及金额{rev}亿元，占上年收入{pcta}%，低于此前披露的执行进度预期。")
_t(Direction.BEARISH, VsExpectation.INLINE, Certainty.CERTAIN, EventType.SHAREHOLDER,
   "{name}股东按计划减持{pcta}%，减持完毕",
   "{name}（{symbol}）公告某股东减持计划实施完毕，累计减持{pcta}%股份，与此前披露的减持计划一致。")
_t(Direction.BEARISH, VsExpectation.BELOW, Certainty.SPECULATIVE, EventType.EARNINGS,
   "传闻{name}四季度毛利率大幅承压，全年或低于预期",
   "市场传闻{name}（{symbol}）四季度主要产品价格下行，毛利率明显承压，全年净利润或低于一致预期{pcta}%以上。公司未予回应，请以公告为准。")
_t(Direction.BEARISH, VsExpectation.BELOW, Certainty.SPECULATIVE, EventType.MA,
   "{name}重组谈判或生变数，交易可能推迟",
   "知情人士称，{name}（{symbol}）正在推进的收购交易在估值条款上出现分歧，交易时间表可能推迟，甚至存在终止风险。该消息尚未获得公司确认。")
_t(Direction.BEARISH, VsExpectation.BELOW, Certainty.CONDITIONAL, EventType.LITIGATION,
   "{name}若败诉将计提大额负债，二审结果待定",
   "{name}（{symbol}）重大诉讼进入二审程序。公司公告提示，若二审维持原判，公司将计提预计负债约{rev}万元，可能对当期利润造成不利影响；目前结果尚不确定。")
_t(Direction.BEARISH, VsExpectation.NO_EXPECTATION, Certainty.SPECULATIVE, EventType.MACRO_POLICY,
   "分析师提醒：若原料价格继续上行，{name}成本压力或加大",
   "有研报提示，{name}（{symbol}）主要原材料价格近期走高，若维持当前趋势，公司下半年成本端或承压，盈利改善节奏可能放缓。该判断基于价格外推，存在不确定性。")
_t(Direction.BEARISH, VsExpectation.INLINE, Certainty.CERTAIN, EventType.PRODUCT,
   "{name}召回部分批次产品，预计影响收入{rev}万元",
   "{name}（{symbol}）公告对部分批次产品实施召回，预计影响当期收入约{rev}万元，与公司此前质量风险提示的范围一致。")

# —— 补齐组合缺口（v2 扩量）：此前加权分布会抽到下列无模板的 (direction, vs) 组合并静默丢事件 ——
_t(Direction.BEARISH, VsExpectation.ABOVE, Certainty.CERTAIN, EventType.LITIGATION,
   "{name}诉讼一审判决：判赔金额低于市场此前担忧的上限",
   "{name}（{symbol}）公告收到一审判决书，判令公司赔偿原告{rev}万元，明显低于市场此前担忧的金额上限。公司已按判决计提预计负债，该判决为一审判决，不再上诉。")
_t(Direction.BEARISH, VsExpectation.ABOVE, Certainty.CERTAIN, EventType.EARNINGS,
   "{name}业绩快报：净利润同比下降，但好于市场担忧的悲观情形",
   "{name}（{symbol}）发布业绩快报，归母净利润{profit}亿元，同比下降{pcta}%。该结果好于市场此前担忧的悲观情形，多家券商认为利空已释放。")
_t(Direction.NEUTRAL, VsExpectation.ABOVE, Certainty.CERTAIN, EventType.OTHER,
   "{name}定增募资落定，总额处于原方案区间上沿",
   "{name}（{symbol}）公告向特定对象发行股票的募资总额{rev}亿元，处于此前披露方案区间的上沿，募集资金已经到账并将用于既定项目。")
_t(Direction.NEUTRAL, VsExpectation.ABOVE, Certainty.CERTAIN, EventType.ORDERS,
   "{name}签订日常销售框架协议，约定金额上限较上年有所提升",
   "{name}（{symbol}）公告与某客户签订日常销售框架协议，约定年度金额上限{rev}亿元，较上年同类协议{pctb}亿元的约定上限有所提升，属于日常经营性业务。")
_t(Direction.NEUTRAL, VsExpectation.BELOW, Certainty.CERTAIN, EventType.OTHER,
   "{name}部分募投项目达到预定可使用状态的时间延后",
   "{name}（{symbol}）公告部分募投项目达到预定可使用状态的时间延后至下一年度，主要受设备到货周期影响，项目原定收益测算保持不变。")
_t(Direction.NEUTRAL, VsExpectation.BELOW, Certainty.CERTAIN, EventType.SHAREHOLDER,
   "{name}年度权益分派实施：每股派息金额较上年略有下调",
   "{name}（{symbol}）公告年度权益分派实施安排，每股现金红利金额较上年略有下调，分红总额约{rev}千万元，公司称现金分红政策保持连续稳定。")

# —— 中性 ——
_t(Direction.NEUTRAL, VsExpectation.NO_EXPECTATION, Certainty.CERTAIN, EventType.SHAREHOLDER,
   "{name}将于下月召开年度股东大会",
   "{name}（{symbol}）公告将于下月{pcta}日召开年度股东大会，审议年度报告及利润分配等议案。")
_t(Direction.NEUTRAL, VsExpectation.NO_EXPECTATION, Certainty.CERTAIN, EventType.OTHER,
   "{name}完成工商变更登记，注册地址变更",
   "{name}（{symbol}）公告已完成相关工商变更登记手续，取得新的营业执照，注册地址发生变更，经营范围保持不变。")
_t(Direction.NEUTRAL, VsExpectation.INLINE, Certainty.CERTAIN, EventType.EARNINGS,
   "{name}披露季度主要经营数据",
   "{name}（{symbol}）披露本季度主要经营数据：营业收入{rev}亿元，同比变动{pcta}%，与此前定期报告披露口径一致。")
_t(Direction.NEUTRAL, VsExpectation.NO_EXPECTATION, Certainty.CERTAIN, EventType.MACRO_POLICY,
   "行业1-{pcta}月运行数据发布，整体平稳",
   "行业协会发布1-{pcta}月行业运行数据，全行业收入同比小幅增长，景气度总体平稳，个体公司表现分化。")
_t(Direction.NEUTRAL, VsExpectation.NO_EXPECTATION, Certainty.CERTAIN, EventType.OTHER,
   "{name}使用部分闲置募集资金进行现金管理",
   "{name}（{symbol}）公告使用不超过{rev}亿元闲置募集资金购买结构性存款，期限十二个月，该事项已经董事会审议通过。")

# 噪声/残缺模板（潜在标签 unknown）
_NOISE_TEMPLATES: list[tuple[str, EventType]] = [
    ("【更正公告】{name}此前公告中财务报表附注存在录入错误，现予更正，更正内容不涉及主营业务数据。", EventType.OTHER),
    ("行业观察：多家上市公司集中在昨日披露业绩，市场关注度较高，具体个股影响需结合基本面分析。", EventType.MACRO_POLICY),
    ("{name}（{symbol}）……（正文缺失，仅有标题残片）", EventType.OTHER),
    ("某不愿具名市场人士表示，短期资金面波动加大，投资者应注意风险，具体标的不做评论。", EventType.MACRO_POLICY),
]

_REPOST_PREFIXES = ("【转载】", "据媒体报道，", "快讯转发：")

# 标签中性公告样板句（v2）：真实公告常见的格式化结尾。约束：不得命中 asg_models 词典
# 任何方向/预期/确定性标记词（由 tests/unit 的零命中测试强制），否则会给 mock/词典
# 预测引入系统性标签偏置。
_FLAVOR_SENTENCES: tuple[str, ...] = (
    "公司将严格按照监管规则做好信息告知与投资者沟通工作。",
    "敬请投资者理性判断，关注市场波动。",
    "公司日常生产经营活动一切正常。",
    "该事项的后续安排以公司正式对外文书为准。",
    "公司各相关部门正有序推进各项准备工作。",
    "涉及的公司内部审批程序均照常进行。",
    "有关详情可通过公司投资者联系渠道咨询。",
    "公司将持续做好经营管理工作，回报广大股东。",
    "公司指定信息渠道刊登的文书为唯一依据。",
    "公司管理层对当前各项业务运转情况表示认可。",
    "上述安排不涉及公司股权结构变动。",
    "公司将于适当时段组织业绩说明交流活动。",
    "相关工作由公司专门团队负责跟进落实。",
    "公司感谢广大股东长期以来的信任。",
    "本次事项不涉及公司主营业务范围调整。",
    "公司提醒各位股东及时查阅相关文书内容。",
)


def _fill(template: str, name: str, symbol: str, rng: np.random.Generator) -> str:
    return template.format(
        name=name,
        symbol=symbol,
        pcta=int(rng.integers(8, 65)),
        pctb=int(rng.integers(3, 80)),
        rev=int(rng.integers(2, 120)),
        profit=int(rng.integers(1, 30)),
    )


def generate_universe(
    *,
    seed: int,
    n_days: int = 180,
    n_events: int = 130,
    repost_ratio: float = 0.25,
    noise_ratio: float = 0.15,
    diversify_flavor: bool = True,
    calendar: TradingCalendar | None = None,
) -> tuple[list[dict], list[SyntheticEvent]]:
    """生成合成文本事件集。返回 (securities, events)，事件按发布时间升序。"""
    cal = calendar or TradingCalendar()
    rng = np.random.default_rng(seed)
    start = cal.next_trading_day(_dt.date(2026, 1, 5), include_self=True)
    days = cal.trading_days(start, _dt.date(start.year + 3, 12, 31))[:n_days]

    securities = [
        {"symbol": s, "exchange": x, "name": n, "industry": i}
        for s, x, n, i in SYNTHETIC_SECURITIES
    ]

    weighted_dirs = [Direction.BULLISH] * 4 + [Direction.BEARISH] * 3 + [Direction.NEUTRAL] * 2
    weighted_vs = (
        [VsExpectation.ABOVE] * 3 + [VsExpectation.BELOW] * 3
        + [VsExpectation.INLINE] * 2 + [VsExpectation.NO_EXPECTATION] * 2
    )
    weighted_cert = [Certainty.CERTAIN] * 4 + [Certainty.SPECULATIVE] * 3 + [Certainty.CONDITIONAL] * 2

    templates_by_key: dict[tuple, list] = {}
    for entry in _T:
        templates_by_key.setdefault(entry[:3], []).append(entry)

    events: list[SyntheticEvent] = []
    seq = 0
    n_noise = int(n_events * noise_ratio)
    n_signal = n_events - n_noise

    def _pick_publish_day() -> _dt.date:
        return days[int(rng.integers(10, len(days) - 5))]

    def _publish_time(day: _dt.date) -> tuple[_dt.datetime, str]:
        hour = int(rng.choice([10, 11, 14, 19, 20]))
        minute = int(rng.integers(0, 59))
        ts = _dt.datetime.combine(day, _dt.time(hour, minute))
        # 约 10% 文本仅日期精度（来源只给到日）
        precision = "date" if rng.random() < 0.10 else "datetime"
        return ts, precision

    for _ in range(n_signal):
        day = _pick_publish_day()
        row = securities[int(rng.integers(0, len(securities)))]
        direction = weighted_dirs[int(rng.integers(0, len(weighted_dirs)))]
        vs = weighted_vs[int(rng.integers(0, len(weighted_vs)))]
        cert = weighted_cert[int(rng.integers(0, len(weighted_cert)))]
        cands = (
            templates_by_key.get((direction, vs, cert))
            or templates_by_key.get((direction, vs, Certainty.SPECULATIVE))
            or templates_by_key.get((direction, vs, Certainty.CERTAIN))
        )
        if not cands:
            continue
        d, v, c, e, title_t, body_t = cands[int(rng.integers(0, len(cands)))]
        ts, precision = _publish_time(day)
        text = _fill(body_t, row["name"], row["symbol"], rng)
        if diversify_flavor:
            # 追加 2 条标签中性样板句：同模板事件的文本可区分（抗 SimHash 误聚），
            # 转载复制原文（含样板句）不受影响。仅信号文本；噪声文本保持原样。
            text += _FLAVOR_SENTENCES[int(rng.integers(0, len(_FLAVOR_SENTENCES)))]
            text += _FLAVOR_SENTENCES[int(rng.integers(0, len(_FLAVOR_SENTENCES)))]
        title = _fill(title_t, row["name"], row["symbol"], rng)
        seq += 1
        latent = LatentLabels(d, v, c, e)
        events.append(
            SyntheticEvent(
                event_seq=seq, symbol=row["symbol"], published_at=ts,
                published_precision=precision, text=text, title=title, latent=latent,
            )
        )
        if rng.random() < repost_ratio:
            for _ in range(int(rng.integers(1, 3))):
                rp_at = ts + _dt.timedelta(hours=int(rng.integers(2, 20)))
                prefix = _REPOST_PREFIXES[int(rng.integers(0, len(_REPOST_PREFIXES)))]
                seq += 1
                events.append(
                    SyntheticEvent(
                        event_seq=seq, symbol=row["symbol"], published_at=rp_at,
                        published_precision="datetime", text=prefix + text, title=title,
                        latent=latent, is_repost=True, repost_of_seq=events[-1].event_seq,
                    )
                )

    for _ in range(n_noise):
        day = _pick_publish_day()
        row = securities[int(rng.integers(0, len(securities)))]
        tpl, e = _NOISE_TEMPLATES[int(rng.integers(0, len(_NOISE_TEMPLATES)))]
        ts, precision = _publish_time(day)
        text = _fill(tpl, row["name"], row["symbol"], rng)
        seq += 1
        events.append(
            SyntheticEvent(
                event_seq=seq, symbol=row["symbol"], published_at=ts,
                published_precision=precision, text=text, title=text[:20],
                latent=LatentLabels(Direction.UNKNOWN, VsExpectation.UNKNOWN, Certainty.UNKNOWN, e),
            )
        )

    events.sort(key=lambda ev: ev.published_at)
    return securities, events


class SyntheticConnector:
    """把合成事件包装为 RawDoc 流（含转载），用于 ingest worker 与测试。"""

    meta = ConnectorMeta(
        source_id=SYNTHETIC_SOURCE_ID,
        display_name="合成演示数据（虚构公司，固定种子）",
        allowed_use="仅用于系统演示与方法验证，不构成任何真实市场信息",
        rate_limit_per_minute=0,
        enabled=True,
    )

    def __init__(
        self,
        *,
        seed: int = 20260907,
        n_events: int = 130,
        n_days: int = 180,
        diversify_flavor: bool = True,
    ):
        self.seed = seed
        self.generator_version = GENERATOR_VERSION
        self._securities, self._events = generate_universe(
            seed=seed, n_events=n_events, n_days=n_days, diversify_flavor=diversify_flavor
        )

    @property
    def securities(self) -> list[dict]:
        return [dict(s) for s in self._securities]

    @property
    def events(self) -> list[SyntheticEvent]:
        return list(self._events)

    def latent_by_seq(self) -> dict[int, LatentLabels]:
        return {e.event_seq: e.latent for e in self._events}

    def fetch_batch(self, **params) -> list[RawDoc]:
        return [
            RawDoc(
                source_id=self.meta.source_id,
                external_id=f"syn-{ev.event_seq}",
                title=ev.title,
                content=ev.text,
                published_at=ev.published_at,
                published_precision=ev.published_precision,
                url=f"https://demo.invalid/syn/{ev.event_seq}",
                symbol_hint=ev.symbol,
                meta={"event_seq": ev.event_seq, "is_repost": ev.is_repost},
            )
            for ev in self._events
        ]

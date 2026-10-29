"""摄取与对齐流水线：RawDoc → SourceDocument/快照 → 清洗 → 聚类 → 实体链接 → MarketText/Event。

时间语义（ADR-003 / DATA_GOVERNANCE）：
- precision=datetime → availability_from_publish 推导；
- precision=date → 保守：下一交易日 15:00（当天任何时刻可能发布）；
- precision=unknown / published_at 缺失 → available_at=None，strict_ok=False，
  严格回测通过 AvailabilityError 拒绝使用。
"""

from __future__ import annotations

import datetime as dt

from asg_connectors.base import RawDoc
from asg_domain.calendar import TradingCalendar, default_calendar
from asg_domain.db import (
    EntityLink,
    Issuer,
    MarketEvent,
    MarketText,
    PriceBar,
    Security,
    SourceDocument,
    SourceSnapshot,
    TextCluster,
    TradingCalendarDay,
    bind_database,
)
from asg_domain.ids import content_hash, new_id, text_hash
from asg_domain.timeutils import AFTERNOON_CLOSE, availability_from_publish
from asg_text_pipeline.dedup import DedupItem, cluster_duplicates
from asg_text_pipeline.entity_link import build_alias_index, link_entities, primary_security
from asg_text_pipeline.normalize import clean_for_analysis
from sqlalchemy import select
from sqlalchemy.orm import Session


def availability_for(doc: RawDoc, cal: TradingCalendar) -> dt.datetime | None:
    """由 RawDoc 推 available_at；无法可靠推导时返回 None（严格模式不可用）。"""
    if doc.published_at is None or doc.published_precision != "datetime":
        if doc.published_at is None:
            return None
        # 仅日期精度：保守推到下一交易日 15:00
        nxt = cal.next_trading_day(doc.published_at.date())
        return dt.datetime.combine(nxt, AFTERNOON_CLOSE)
    return availability_from_publish(doc.published_at, next_trading_day=cal.next_trading_day)


def strict_eligible(doc: RawDoc) -> bool:
    return doc.published_at is not None and doc.published_precision == "datetime"


def ingest_documents(
    session: Session,
    docs: list[RawDoc],
    *,
    cal: TradingCalendar | None = None,
    event_layer_fallback: bool = False,
) -> dict:
    """入库 + 清洗 + 聚类 + 链接（幂等：external_id 相同且内容哈希相同 → 跳过）。

    event_layer_fallback（ADR-015，v4 预注册开启）：SimHash 聚类之后接事件层
    拆分/合并兜底（refine_clusters；security_of 取实体链接得到的 security_id，
    签名对转载前缀不变式的教训见 ADR-015）。**默认 False**：聚类路径与历史
    版本逐位一致，研究指标不变（等价性三层防线测试守护）。

    返回统计 {n_input, n_ingested, n_dupe_ingest, n_texts, n_clusters, n_events, n_quarantine_align}。
    """
    cal = cal or default_calendar()

    # 幂等检查：现有 (source_id, external_id) → content_hash
    existing = session.execute(
        select(SourceDocument).where(SourceDocument.source_id.in_({d.source_id for d in docs} or {""}))
    ).scalars().all()
    known: dict[str, tuple[str, str]] = {}  # external_id -> (source_id, content_hash)
    for sd in existing:
        ext = (sd.meta or {}).get("external_id", "")
        known[ext] = (sd.source_id, sd.content_hash)

    securities = session.execute(select(Security)).scalars().all()
    alias_index = build_alias_index([(s.name, s.security_id, s.issuer_id or "") for s in securities])
    symbol_map = {s.symbol: (s.security_id, s.issuer_id) for s in securities}

    n_ingested = n_dupe = 0
    new_docs: list[tuple[RawDoc, SourceDocument]] = []
    for doc in docs:
        chash = content_hash(doc.content.encode("utf-8"))
        prev = known.get(doc.external_id)
        if prev and prev[0] == doc.source_id and prev[1] == chash:
            n_dupe += 1
            continue
        sd = SourceDocument(
            # 批量落库（阶段 15）：预生成主键，整批一次 flush（此前每文档
            # 一次 flush；ID 为 uuid4 与研究指标无关，等价性测试守护）。
            source_doc_id=new_id("doc"),
            source_id=doc.source_id,
            url=doc.url,
            title=doc.title[:250],
            raw_content=doc.content,
            content_hash=chash,
            published_at=doc.published_at,
            published_at_precision=doc.published_precision,
            meta={"external_id": doc.external_id, "symbol_hint": doc.symbol_hint},
        )
        session.add(sd)
        session.add(
            SourceSnapshot(
                source_doc_id=sd.source_doc_id,
                storage_uri=f"local://raw/{sd.source_doc_id}.txt",
                content_hash=chash,
                meta={"bytes": len(doc.content.encode("utf-8"))},
            )
        )
        known[doc.external_id] = (doc.source_id, chash)
        new_docs.append((doc, sd))
        n_ingested += 1
    session.flush()

    # 清洗 + MarketText
    n_quarantine_align = 0
    text_rows: list[tuple[RawDoc, SourceDocument, MarketText]] = []
    link_rows: list[EntityLink] = []
    for doc, sd in new_docs:
        cleaned = clean_for_analysis(doc.content)
        if len(cleaned) < 8:
            n_quarantine_align += 1
            continue
        mentions = link_entities(cleaned, alias_index=alias_index, resolve_code=symbol_map.get)
        # 实体对齐失败（symbol_hint 有但无法链接）→ 隔离队列，不入 MarketText
        sec_id = primary_security(mentions)
        if sec_id is None:
            if doc.symbol_hint and doc.symbol_hint in symbol_map:
                sec_id, iss_id = symbol_map[doc.symbol_hint]
            else:
                n_quarantine_align += 1
                continue
        else:
            iss_id = next((m.issuer_id for m in mentions if m.issuer_id), None)
        avail = availability_for(doc, cal)
        mt = MarketText(
            market_text_id=new_id("mtx"),
            source_doc_id=sd.source_doc_id,
            security_id=sec_id,
            issuer_id=iss_id,
            text_type="news",
            title=sd.title,
            cleaned_text=cleaned,
            text_hash=text_hash(cleaned),
            published_at=doc.published_at,
            available_at=avail,
            strict_ok=strict_eligible(doc),
            quality_flags={"precision": doc.published_precision, "n_mentions": len(mentions)},
        )
        session.add(mt)
        link_rows.extend(
            EntityLink(
                market_text_id=mt.market_text_id,
                issuer_id=m.issuer_id or None,
                security_id=m.security_id,
                method=m.method,
                span_start=m.span_start,
                span_end=m.span_end,
                confidence=m.confidence,
            )
            for m in mentions
        )
        text_rows.append((doc, sd, mt))
    # 先落文本行、再落实体链接（批量,阶段 15）：两表无 relationship,同批 flush
    # 不保证跨表先后（实测 entity_links 会先于 market_texts 执行并触发 FK 失败）
    session.flush()
    session.add_all(link_rows)
    session.flush()

    # 转载聚类（清洗后文本）：先建簇行，再回填文本的 cluster_id（外键顺序）
    items = [DedupItem(item_id=mt.market_text_id, text=mt.cleaned_text, published_at=mt.published_at) for _, _, mt in text_rows]
    clustering = cluster_duplicates(items)
    cluster_method = "simhash_v1"
    if event_layer_fallback:
        # 事件层兜底（ADR-015）：security_of 来自实体链接的 primary security
        from asg_text_pipeline.event_layer import refine_clusters

        security_of = {mt.market_text_id: (mt.security_id or "") for _, _, mt in text_rows}
        clustering = refine_clusters(items, clustering, security_of=security_of)
        cluster_method = "simhash_v1+event_layer_v1"
    n_clusters = clustering.n_clusters
    text_by_id = {m.market_text_id: m for _, _, m in text_rows}
    for cid, first_item_id in clustering.canonical_of.items():
        first_mt = text_by_id[first_item_id]
        session.add(
            TextCluster(
                cluster_id=cid,
                canonical_doc_id=first_item_id,
                first_published_at=first_mt.published_at,
                method=cluster_method,
            )
        )
    session.flush()
    for mt_item_id, cid in clustering.cluster_of.items():
        text_by_id[mt_item_id].cluster_id = cid
    session.flush()

    # 事件层：仅簇首发（canonical）文本生成 MarketEvent
    n_events = 0
    for cid, canonical_id in clustering.canonical_of.items():
        mt = text_by_id[canonical_id]
        event = MarketEvent(
            market_text_id=canonical_id,
            cluster_id=cid,
            event_type="other",
            summary=mt.title[:200],
            effective_at=mt.published_at,
        )
        session.add(event)
        n_events += 1

    session.commit()
    return {
        "n_input": len(docs),
        "n_ingested": n_ingested,
        "n_dupe_ingest": n_dupe,
        "n_texts": len(text_rows),
        "n_clusters": n_clusters,
        "n_events": n_events,
        "n_quarantine_align": n_quarantine_align,
    }


def upsert_market_data(
    session: Session,
    *,
    securities: list[dict],
    bars: dict[str, list[dict]],
    cal: TradingCalendar | None = None,
    bind_seed: int | None = None,
    bind_generator_version: str | None = None,
    bind_allow_adopt: bool = False,
) -> dict:
    """写入 Issuer/Security/行情/物化日历（幂等：重复 (symbol, date) 跳过）。

    绑定接入点（ADR-014）：bind_seed 提供时，首次写入前落库级绑定
    （seed_demo 由此在首次播种即绑定；不带 bind 参数的直写不改变绑定状态——
    未绑定库写过数据后实验默认被拒，见 require_database_binding）。
    """
    if bind_seed is not None or bind_generator_version is not None:
        bind_database(
            session,
            seed=bind_seed,
            generator_version=bind_generator_version or "unknown",
            allow_adopt=bind_allow_adopt,
        )
    cal = cal or default_calendar()
    n_secs = n_bars = 0
    issuer_cache: dict[str, str] = {}
    for s in securities:
        exists = session.execute(select(Security).where(Security.symbol == s["symbol"], Security.exchange == s["exchange"])).scalar_one_or_none()
        if exists is not None:
            continue
        iss_name = f"{s['name']}（发行人）"
        if iss_name not in issuer_cache:
            issuer = Issuer(name=iss_name, short_name=s["name"], industry=s.get("industry", ""))
            session.add(issuer)
            session.flush()
            issuer_cache[iss_name] = issuer.issuer_id
        sec = Security(symbol=s["symbol"], exchange=s["exchange"], name=s["name"], issuer_id=issuer_cache[iss_name])
        session.add(sec)
        session.flush()
        n_secs += 1
    session.flush()

    all_secs = session.execute(select(Security)).scalars().all()
    sym2id = {s.symbol: s.security_id for s in all_secs}
    existing_bars = {(r[0], r[1]) for r in session.execute(select(PriceBar.security_id, PriceBar.trade_date)).all()}
    for sym, series in bars.items():
        sec_id = sym2id.get(sym)
        if sec_id is None:
            continue
        for b in series:
            if (sec_id, b["trade_date"]) in existing_bars:
                continue
            session.add(
                PriceBar(
                    security_id=sec_id,
                    trade_date=b["trade_date"],
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    pre_close=b.get("pre_close"),
                    volume=b.get("volume", 0.0),
                    amount=b.get("amount", 0.0),
                    limit_up=b.get("limit_up"),
                    limit_down=b.get("limit_down"),
                    suspended=b.get("suspended", False),
                    adjusted=True,
                    source="synthetic_v1",
                )
            )
            n_bars += 1
    session.flush()

    # 物化日历：覆盖行情期间
    all_days = sorted({b["trade_date"] for series in bars.values() for b in series})
    for d in all_days:
        exists = session.get(TradingCalendarDay, d)
        if exists is None:
            session.add(TradingCalendarDay(day=d, is_trading=cal.is_trading_day(d)))
    session.commit()
    return {"n_new_securities": n_secs, "n_new_bars": n_bars, "n_days": len(all_days)}

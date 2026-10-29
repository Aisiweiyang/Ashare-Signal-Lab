"""文本清洗、去重聚类、实体链接、数据质量、contract 单元测试。"""

from __future__ import annotations

import datetime as dt

import pytest
from asg_domain.errors import AvailabilityError
from asg_domain.quality import (
    expect_available_at_covers_published,
    expect_labels_in_domain,
    expect_no_duplicate_clusters_across_sets,
    expect_no_future_rows,
    expect_not_null,
    expect_price_continuity,
    expect_unique,
    require_available,
    run_all,
)
from asg_text_pipeline import (
    DedupItem,
    anonymize,
    build_alias_index,
    clean_for_analysis,
    cluster_duplicates,
    hamming,
    link_entities,
    normalize,
    parse_publish_time,
    simhash,
    strip_html,
)


class TestNormalize:
    def test_fullwidth_to_halfwidth(self):
        assert normalize("增长２５％") == "增长25%"

    def test_html_strip_and_whitespace(self):
        t = strip_html("<p>公司  公告</p>　全文")
        cleaned = normalize(t)
        assert "<" not in cleaned
        assert "　" not in cleaned

    def test_anonymize_phone_and_id(self):
        t = anonymize("联系13812345678，证件110101199003077758，卡号622202123456789012")
        assert "13812345678" not in t
        assert "110101199003077758" not in t
        assert "622202123456789012" not in t
        assert "[手机号]" in t and "[证件号]" in t

    def test_clean_for_analysis_pipeline(self):
        out = clean_for_analysis("<b>净利润</b>同比增长，详见 https://x.cn/a?token=abc")
        assert "https" not in out
        assert "净利润" in out

    def test_parse_publish_time_precision(self):
        assert parse_publish_time("2026-03-02 10:30:00") == (dt.datetime(2026, 3, 2, 10, 30), "datetime")
        assert parse_publish_time("2026年3月2日 10:30") == (dt.datetime(2026, 3, 2, 10, 30), "datetime")
        assert parse_publish_time("2026-03-02")[1] == "date"
        assert parse_publish_time("随便")[0] is None
        assert parse_publish_time(None)[1] == "unknown"
        assert parse_publish_time(dt.datetime(2026, 1, 1, 8, 0))[1] == "datetime"


class TestDedup:
    def test_simhash_deterministic_and_stable(self):
        a = simhash("公司公告业绩快报，净利润大增")
        b = simhash("公司公告业绩快报，净利润大增")
        assert a == b
        assert a != 0

    def test_simhash_similar_texts_close(self):
        base = "云舟数控发布业绩快报，营业收入同比增长45%，净利润超出市场一致预期"
        h1 = simhash(base)
        h2 = simhash("【转载】" + base)
        assert hamming(h1, h2) <= 6

    def test_hamming(self):
        assert hamming(0b1010, 0b1010) == 0
        assert hamming(0b1010, 0b0101) == 4

    def test_cluster_keeps_first_published(self):
        items = [
            DedupItem("a", "公司公告业绩快报净利润大增百分之四十五超出预期", dt.datetime(2026, 3, 2, 19, 0)),
            DedupItem("b", "【转载】公司公告业绩快报净利润大增百分之四十五超出预期", dt.datetime(2026, 3, 2, 21, 0)),
            DedupItem("c", "另一家公司的订单合同签署公告与上述无关", dt.datetime(2026, 3, 3, 10, 0)),
        ]
        res = cluster_duplicates(items)
        cid = res.cluster_of["a"]
        assert res.cluster_of["b"] == cid
        assert res.cluster_of["c"] != cid
        assert res.canonical_of[cid] == "a"  # 首发为 a
        assert res.n_clusters == 2

    def test_cluster_empty(self):
        res = cluster_duplicates([])
        assert res.n_clusters == 0


class TestEntityLink:
    def test_alias_index_rejects_single_char(self):
        with pytest.raises(ValueError):
            build_alias_index([("云", "sec_1", "ent_1")])

    def test_alias_index_conflict(self):
        with pytest.raises(ValueError):
            build_alias_index([("云舟", "sec_1", "ent_1"), ("云舟", "sec_2", "ent_2")])

    def test_code_with_suffix(self):
        ms = link_entities("买入601907.SH的公告分析")
        assert any(m.symbol == "601907" for m in ms)

    def test_bare_code_unknown_prefix_ignored(self):
        assert link_entities("编号999999的新闻") == []

    def test_alias_and_overlap_preference(self):
        idx = build_alias_index([("云舟数控", "sec_x", "ent_x"), ("数控", "sec_y", "ent_y")])
        ms = link_entities("云舟数控发布公告", alias_index=idx)
        # 重叠跨度保留最长（云舟数控），不再命中「数控」
        assert len(ms) == 1 and ms[0].security_id == "sec_x"

    def test_primary_security(self):
        idx = build_alias_index([("岚台生物", "sec_b", "ent_b")])
        ms = link_entities("岚台生物（600382）公告", alias_index=idx)
        assert ms
        assert ms[0].security_id == "sec_b"


class TestQualityContracts:
    def test_no_future_rows(self):
        now = dt.datetime(2026, 3, 2, 12, 0)
        rows = [
            {"id": "a", "available_at": dt.datetime(2026, 3, 1, 15, 0)},
            {"id": "b", "available_at": dt.datetime(2026, 3, 3, 15, 0)},
        ]
        res = expect_no_future_rows(rows, now)
        assert not res["passed"] and res["violations"] == ["b"]

    def test_unique_and_not_null(self):
        rows = [{"id": 1, "k": "x"}, {"id": 2, "k": "x"}, {"id": 3, "k": None}]
        assert not expect_unique(rows, "k")["passed"]
        assert not expect_not_null(rows, "k")["passed"]
        assert expect_unique(rows, "id")["passed"]

    def test_labels_domain(self):
        ok = expect_labels_in_domain([{"id": 1, "direction": "bullish", "vs_expectation": "above", "certainty": "certain"}])
        assert ok["passed"]
        bad = expect_labels_in_domain([{"id": 1, "direction": "涨", "vs_expectation": "above", "certainty": "certain"}])
        assert not bad["passed"]

    def test_price_continuity(self):
        days = [dt.date(2026, 3, 2), dt.date(2026, 3, 3)]
        full = {"S1": [{"trade_date": d} for d in days]}
        assert expect_price_continuity(full, days)["passed"]
        gapped = {"S1": [{"trade_date": days[0]}]}
        assert not expect_price_continuity(gapped, days)["passed"]

    def test_available_at_covers_published(self):
        rows = [
            {"id": 1, "published_at": dt.datetime(2026, 3, 1, 10, 0), "available_at": dt.datetime(2026, 3, 1, 15, 0)},
            {"id": 2, "published_at": dt.datetime(2026, 3, 1, 10, 0), "available_at": None},
            {"id": 3, "published_at": dt.datetime(2026, 3, 2, 10, 0), "available_at": dt.datetime(2026, 3, 1, 15, 0)},
        ]
        res = expect_available_at_covers_published(rows)
        assert not res["passed"] and len(res["violations"]) == 2

    def test_cluster_leak_detection(self):
        train = [{"cluster_id": "c1"}, {"cluster_id": "c2"}]
        test = [{"cluster_id": "c1"}]
        assert not expect_no_duplicate_clusters_across_sets(train, test)["passed"]
        test_ok = [{"cluster_id": "c3"}]
        assert expect_no_duplicate_clusters_across_sets(train, test_ok)["passed"]

    def test_require_available_gates(self):
        t = dt.datetime(2026, 3, 2, 15, 0)
        require_available(t, t, "ok")
        with pytest.raises(AvailabilityError):
            require_available(dt.datetime(2026, 3, 3, 15, 0), t, "future")
        with pytest.raises(AvailabilityError):
            require_available(None, t, "missing")

    def test_run_all_aggregates(self):
        result = run_all(
            [{"k": 1}],
            [lambda rows: {"name": "a", "passed": True, "violations": []}, lambda rows: {"name": "b", "passed": False, "violations": ["x"]}],
        )
        assert result["passed"] is False and result["n_assertions"] == 2

"""连接器注册表：注册/创建/停用/entry-points 插件发现与失败隔离。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from asg_connectors import REGISTRY, ConnectorMeta, ConnectorRegistry, RawDoc, SyntheticConnector
from asg_connectors.registry import ENTRY_POINT_GROUP
from asg_domain.errors import ConnectorError, ConnectorUnavailableError


class _DummyConnector:
    def __init__(self, *, source_id: str = "dummy_src"):
        self.meta = ConnectorMeta(source_id=source_id, display_name="dummy", allowed_use="test")

    def fetch_batch(self, **_params) -> list[RawDoc]:
        return []


class TestRegistryCore:
    def test_register_and_create(self):
        reg = ConnectorRegistry()
        reg.register("src_a", lambda: _DummyConnector(source_id="src_a"))
        inst = reg.create("src_a")
        assert inst.meta.source_id == "src_a"
        assert isinstance(inst, _DummyConnector)

    def test_duplicate_registration_rejected(self):
        reg = ConnectorRegistry()
        reg.register("src_a", _DummyConnector)
        with pytest.raises(ConnectorError, match="已注册"):
            reg.register("src_a", _DummyConnector)

    def test_unknown_source_raises_unavailable(self):
        with pytest.raises(ConnectorUnavailableError):
            REGISTRY.create("no_such_source")

    def test_meta_source_id_must_match_key(self):
        reg = ConnectorRegistry()
        reg.register("key_a", _DummyConnector)  # 产出 meta.source_id="dummy_src" ≠ key
        with pytest.raises(ConnectorError, match="不一致"):
            reg.create("key_a")

    def test_disable_switch(self):
        reg = ConnectorRegistry()
        reg.register("src_a", lambda: _DummyConnector(source_id="src_a"))
        reg.set_disabled("src_a")
        assert reg.list_connectors()[0].disabled is True
        with pytest.raises(ConnectorUnavailableError, match="停用"):
            reg.create("src_a")
        reg.set_disabled("src_a", disabled=False)
        reg.create("src_a")  # 重新启用后可再次创建

    def test_factory_must_be_callable(self):
        reg = ConnectorRegistry()
        with pytest.raises(ConnectorError):
            reg.register("bad", "not-callable")  # type: ignore[arg-type]


class TestBuiltinRegistration:
    def test_builtin_sources_present(self):
        ids = {r.source_id for r in REGISTRY.list_connectors()}
        assert "synthetic_demo_v1" in ids
        assert "akshare_hist_daily" in ids

    def test_synthetic_create_is_parameterizable(self):
        default = REGISTRY.create("synthetic_demo_v1")
        assert isinstance(default, SyntheticConnector)
        # 默认宇宙量级（模板候选缺失的槽位会被跳过，精确数随种子浮动，只断言规模下界）
        assert len(default.events) >= 100
        # 参数化：不同 seed 产出不同事件流
        other = REGISTRY.create("synthetic_demo_v1", seed=42, n_events=20, n_days=60)
        assert len(other.events) >= 20
        assert [e.published_at for e in other.events] != [e.published_at for e in default.events][
            : len(other.events)
        ]

    def test_akshare_default_disabled(self):
        inst = REGISTRY.create("akshare_hist_daily")
        assert inst.meta.enabled is False


class TestPluginDiscovery:
    def _mk_ep(self, name: str, value: str, obj: object, dist: str = "fake-plugin"):
        return SimpleNamespace(
            name=name, value=value, load=lambda: obj, dist=SimpleNamespace(name=dist)
        )

    def test_discover_registers_valid_plugin(self, monkeypatch):
        reg = ConnectorRegistry()

        def factory():
            return _DummyConnector(source_id="plugin_src")

        eps = [self._mk_ep("plugin_src", "mod:factory", factory)]
        monkeypatch.setattr(
            "asg_connectors.registry.importlib.metadata.entry_points",
            lambda **kw: eps if kw.get("group") == ENTRY_POINT_GROUP else [],
        )
        log = reg.discover_plugins()
        assert any(x.startswith("ok: plugin_src") for x in log)
        reg.create("plugin_src")

    def test_discover_isolates_broken_plugin(self, monkeypatch):
        reg = ConnectorRegistry()

        def broken():
            raise RuntimeError("boom")

        def no_meta():
            return object()

        eps = [
            self._mk_ep("broken", "mod:broken", broken),
            self._mk_ep("no_meta", "mod:no_meta", no_meta),
        ]
        monkeypatch.setattr(
            "asg_connectors.registry.importlib.metadata.entry_points",
            lambda **kw: eps,
        )
        log = reg.discover_plugins()
        assert len([x for x in log if x.startswith("skipped:")]) == 2
        assert reg.list_connectors() == []

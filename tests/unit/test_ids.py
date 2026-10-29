"""ids 模块工具函数测试（阶段 22 守护网刷新：覆盖此前无直接测试的辅助函数）。"""

from __future__ import annotations

from datetime import date, datetime

from asg_domain.ids import (
    canonical_env,
    config_hash,
    content_hash,
    file_hash,
    new_id,
    short_token,
    stable_json,
    text_hash,
)


def test_new_id_prefix_and_uniqueness():
    ids = {new_id("usr") for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("usr_") and len(i) == 4 + 12 for i in ids)


def test_short_token_hex_length():
    assert len(short_token()) == 16
    assert len(short_token(8)) == 8
    int(short_token(), 16)  # 全 hex


def test_content_and_text_hash_consistency():
    assert content_hash(b"abc") == content_hash(b"abc")
    assert content_hash(b"abc") != content_hash(b"abd")
    assert text_hash("云舟") == content_hash("云舟".encode())


def test_file_hash_matches_content_hash(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"payload-123")
    assert file_hash(p) == content_hash(b"payload-123")


def test_stable_json_deterministic_and_sorted():
    a = stable_json({"b": 1, "a": [3, 1]})
    b = stable_json({"a": [3, 1], "b": 1})
    assert a == b == '{"a":[3,1],"b":1}'
    assert stable_json({"s": {3, 1, 2}}) == stable_json({"s": {1, 2, 3}})  # set 排序
    assert "2026-01-05" in stable_json({"d": date(2026, 1, 5)})
    assert "T10:00:00" in stable_json({"d": datetime(2026, 1, 5, 10)})


def test_config_hash_changes_with_content():
    h1 = config_hash({"x": 1})
    h2 = config_hash({"x": 2})
    assert h1 != h2 and len(h1) == 64


def test_canonical_env_has_no_secrets():
    env = canonical_env()
    assert set(env) == {"python", "platform", "machine"}
    assert env["python"]


def test_current_code_commit_fallback_unknown(monkeypatch):
    """git 不可用时返回 unknown 且不抛错（绑定记录不能因环境缺 git 而阻塞）。"""
    import subprocess

    from asg_domain.db import current_code_commit

    def _boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert current_code_commit() == "unknown"

"""启动回归：重复启动与校验失败保留数据，依赖失败不继续播种。"""

from __future__ import annotations

import hashlib
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from asg_domain.db import BindingError, Security, init_db, make_engine, make_session_factory
from asg_pipeline import seed_demo

ROOT = Path(__file__).resolve().parents[2]
prepare_demo = runpy.run_path(str(ROOT / "scripts" / "prepare_demo.py"))["prepare_demo"]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_repeated_start_reuses_database_without_writing(tmp_path):
    path = tmp_path / "demo.db"
    url = f"sqlite:///{path.as_posix()}"
    assert prepare_demo(url)["status"] == "seeded"
    before = digest(path)
    assert prepare_demo(url) == {"status": "reused", "seed": 20260907}
    assert digest(path) == before


@pytest.mark.parametrize("legacy", [True, False])
def test_incompatible_database_is_preserved(tmp_path, legacy):
    path = tmp_path / "research.db"
    url = f"sqlite:///{path.as_posix()}"
    engine = make_engine(url)
    init_db(engine)
    sf = make_session_factory(engine)
    with sf() as session:
        if legacy:
            session.add(Security(symbol="000001", exchange="SZSE", name="已有研究数据"))
            session.commit()
        else:
            seed_demo(session, seed=42, n_events=30, n_days=90)
    engine.dispose()
    before = digest(path)
    with pytest.raises(BindingError):
        prepare_demo(url)
    assert digest(path) == before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 启动器")
def test_launcher_stops_when_dependency_sync_fails(tmp_path):
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell 未安装")
    (tmp_path / "uv.cmd").write_text("@echo off\nexit /b 23\n", encoding="ascii")
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/demo.ps1", "-NoServe"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert b"23" in result.stderr
    assert b"[2/4]" not in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell legacy encoding")
def test_launcher_parses_when_default_codepage_is_western():
    """Simulate a non-Chinese Windows 5.1 reader without changing the OS locale."""
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell unavailable")
    probe = r"""
$stream = [IO.File]::OpenRead('scripts/demo.ps1')
$reader = New-Object IO.StreamReader($stream, [Text.Encoding]::GetEncoding(1252), $true)
try { $source = $reader.ReadToEnd() } finally { $reader.Dispose() }
$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { Write-Error ($parseErrors | Out-String); exit 1 }
"""
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", probe],
        cwd=ROOT, capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")

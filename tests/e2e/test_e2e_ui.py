"""Playwright E2E：完整用户旅程（验收项 7）。

导入演示数据 → 浏览文本 → 标注 → 一致性 → 运行基线/实验 → 查看结果 → 导出。
浏览器不可用时跳过（skip），API 层等价流程已由 tests/integration/test_api_flow.py 覆盖。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def live_server():
    """起一个真实 uvicorn 服务（独立测试库 + 已播种演示数据）。"""
    import tempfile

    import uvicorn
    from asg_api.main import create_app, ensure_demo_users
    from asg_domain.db import init_db, make_engine, make_session_factory
    from asg_pipeline import seed_demo

    db_dir = Path(tempfile.mkdtemp(prefix="e2e-", dir=Path(__file__).resolve().parents[2] / ".pytest_tmp"))
    db_dir.mkdir(exist_ok=True)
    engine = make_engine(f"sqlite:///{(db_dir / 'e2e.db').as_posix()}")
    init_db(engine)
    sf = make_session_factory(engine)
    ensure_demo_users(sf)
    with sf() as s:
        # ADR-014：库在首次播种即绑定 seed/generator_version；实验表单默认
        # seed=20260907/n_events=130，播种参数与之一致，避免绑定校验拒绝
        # （旧代码下此处会静默产生「行情来自旧 seed、事件来自新 seed」的混库数据）。
        seed_demo(s, seed=20260907, n_events=130)

    app = create_app()
    app.state.engine = engine
    app.state.db_session_factory = sf

    config = uvicorn.Config(app, host="127.0.0.1", port=8399, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.2)
    yield "http://127.0.0.1:8399"
    server.should_exit = True
    t.join(timeout=5)
    engine.dispose()


def test_full_user_journey(live_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:  # pragma: no cover - 浏览器未安装
            pytest.skip(f"chromium 不可用，跳过 UI E2E（API 层已覆盖）: {e}")
        page = browser.new_page()
        base = live_server

        # 1) 登录（研究员）
        page.goto(base)
        page.fill('input[placeholder="用户名"]', "researcher")
        page.fill('input[placeholder="密码"]', "Demo12345!")
        page.get_by_role("button", name="登录", exact=True).click()
        page.wait_for_selector("text=系统状态", timeout=15000)

        # 2) 文本浏览
        page.get_by_role("button", name="文本浏览").click()
        page.wait_for_selector("table", timeout=10000)

        # 3) 标注工作台（提交一条标注）
        page.get_by_role("button", name="标注工作台").click()
        page.wait_for_selector("table", timeout=10000)
        page.get_by_role("button", name="打开").first.click()
        page.wait_for_selector("text=提交标注", timeout=10000)
        page.select_option("select >> nth=0", "bullish")
        page.select_option("select >> nth=1", "above")
        page.select_option("select >> nth=2", "certain")
        page.get_by_role("button", name="提交标注").click()
        page.wait_for_selector("text=已保存", timeout=10000)

        # 4) 运行实验并查看 H1 判定
        page.get_by_role("button", name="实验中心").click()
        page.wait_for_selector("text=运行新实验", timeout=10000)
        page.click("button:has-text('运行')")
        page.wait_for_selector("text=实验结果", timeout=180000)
        page.wait_for_selector("text=H1（金标信号）", timeout=10000)

        # 5) 数据质量页
        page.get_by_role("button", name="数据质量").click()
        page.wait_for_selector("text=断言", timeout=10000)

        browser.close()


def test_bilingual_analysis_user_journey(live_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            pytest.skip(f"chromium 不可用: {e}")
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server)
        page.get_by_placeholder("用户名").fill("researcher")
        page.get_by_placeholder("密码").fill("Demo12345!")
        page.get_by_role("button", name="登录", exact=True).click()
        page.get_by_role("button", name="双语情绪分析", exact=True).click()
        page.get_by_role("button", name="分析文本", exact=True).click()
        page.get_by_text("低于预期", exact=True).first.wait_for()
        page.get_by_text("偏利空", exact=True).first.wait_for()
        page.get_by_role("button", name="英文预期样例", exact=True).click()
        page.get_by_role("button", name="分析文本", exact=True).click()
        page.get_by_text("低于预期", exact=True).first.wait_for()
        output = Path(__file__).resolve().parents[2]/"output/playwright"
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output/"bilingual-analysis-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.screenshot(path=str(output/"bilingual-analysis-mobile.png"), full_page=True)
        page.get_by_role("button", name="中文单位样例", exact=True).click()
        page.get_by_role("button", name="分析文本", exact=True).click()
        page.get_by_text("超出预期", exact=True).first.wait_for()
        page.get_by_text("数值比较：利润率（变化量）实际 0.5 / 预期 0.4 个百分点", exact=True).wait_for()
        page.get_by_role("button", name="英文否定样例", exact=True).click()
        page.get_by_role("button", name="分析文本", exact=True).click()
        page.get_by_text("偏利空", exact=True).first.wait_for()
        page.get_by_label("目标公司", exact=True).fill("MissingCompany")
        page.get_by_role("button", name="分析文本", exact=True).click()
        page.get_by_text("未找到目标公司", exact=False).first.wait_for()
        page.set_viewport_size({"width": 1280, "height": 1000})
        page.get_by_role("button", name="文本浏览", exact=True).click()
        page.get_by_role("link", name="查看", exact=True).first.click()
        page.get_by_role("button", name="分析此文本情绪", exact=True).click()
        page.get_by_text("情绪分析结果", exact=True).wait_for()
        assert not errors
        browser.close()

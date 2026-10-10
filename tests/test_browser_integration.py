"""Integration test against a REAL Chromium via Playwright.

Skipped automatically when Chromium is not installed (run `make setup`).
Uses the retro bank sample, exercising element extraction, coordinate clicks,
hash navigation, and screenshots.
"""

import io
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import sync_playwright  # noqa: E402

from hivemind.browser import PlaywrightBrowserFactory  # noqa: E402
from hivemind.config import Config  # noqa: E402
from hivemind.schemas import Action  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _chromium_available() -> bool:
    try:
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
            return Path(path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_available(), reason="Chromium not installed (run: make setup)")


def test_real_browser_navigates_retro_site():
    config = Config.from_env(mode="mock", headless=True, viewport_w=1280, viewport_h=800)
    factory = PlaywrightBrowserFactory(config)
    browser = factory.open()
    try:
        browser.goto((SAMPLES / "retro_bank.html").as_uri())
        state = browser.state()
        labels = [el.label for el in state.elements]
        assert "他行振込" in labels, labels
        assert "閉じる" in labels
        assert "みなと銀行" in state.title or "みなと銀行" in state.text_digest

        shot = browser.screenshot()
        assert shot[:8] == b"\x89PNG\r\n\x1a\n"
        Image = None
        try:
            from PIL import Image
        except ImportError:
            pass
        if Image is not None:
            assert Image.open(io.BytesIO(shot)).size == (1280, 800)

        target = next(el for el in state.elements if el.label == "他行振込")
        perform = browser.perform(Action(type="click", element=target.index, thought="", emotion="curious"), state)
        assert perform.ok
        assert "#transfer" in perform.new_url

        state2 = browser.state()
        assert "通常払込み" in state2.text_digest or "他行振込" in state2.text_digest
    finally:
        browser.close()
        factory.close()

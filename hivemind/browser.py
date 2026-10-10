"""Real browser control via Playwright (the "hands" of Phase 2).

One browser context per persona. The agent sees:
- a numbered element list (index, kind, label, size, position)
- a text digest of the page
- an optional screenshot (vision mode)

Actions are performed coordinate-first (mouse.move + mouse.click), exactly like
a human would, so the recorded cursor trace is faithful.
"""

from __future__ import annotations

import time

from .agent import PerformResult
from .config import Config
from .schemas import Action, Element, PageState

EXTRACT_ELEMENTS_JS = """() => {
  const out = [];
  const nodes = document.querySelectorAll('a, button, input, select, textarea, [role="button"], [onclick]');
  const vw = window.innerWidth, vh = window.innerHeight;
  for (const n of nodes) {
    const r = n.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue;
    let label = (n.getAttribute('aria-label') || '').trim();
    if (!label) label = (n.innerText || '').trim().replace(/\\s+/g, ' ');
    if (!label) label = String(n.value || n.placeholder || n.title || n.alt || '').trim();
    if (!label) label = n.tagName === 'INPUT' ? (n.type || 'input') : n.tagName.toLowerCase();
    const kind = n.tagName === 'A' ? 'link'
      : (n.tagName === 'BUTTON'
         || (n.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(n.type))
         || n.getAttribute('role') === 'button') ? 'button'
      : ['INPUT', 'SELECT', 'TEXTAREA'].includes(n.tagName) ? 'input' : 'other';
    out.push({
      index: out.length, tag: n.tagName.toLowerCase(), role: n.getAttribute('role'),
      kind, label: label.slice(0, 80),
      x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)
    });
    if (out.length >= 80) break;
  }
  return out;
}"""

TEXT_DIGEST_JS = """() => {
  const t = document.body ? document.body.innerText : '';
  return t.replace(/\\n{3,}/g, '\\n\\n').slice(0, 1500);
}"""


def _element(state: PageState, index: int | None) -> Element | None:
    if index is None or not (0 <= index < len(state.elements)):
        return None
    return state.elements[index]


class PlaywrightBrowser:
    """A single persona's browser context."""

    def __init__(self, config: Config, context) -> None:
        self.config = config
        self.context = context
        self.page = context.new_page()

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        self.page.wait_for_timeout(300)

    def state(self) -> PageState:
        elements = [Element(**el) for el in self.page.evaluate(EXTRACT_ELEMENTS_JS)]
        digest = self.page.evaluate(TEXT_DIGEST_JS)
        return PageState(
            url=self.page.url,
            title=self.page.title() or "",
            elements=elements,
            text_digest=digest or "",
            viewport_w=self.config.viewport_w,
            viewport_h=self.config.viewport_h,
        )

    def screenshot(self) -> bytes:
        return self.page.screenshot(type="png")

    def perform(self, action: Action, state: PageState) -> PerformResult:
        try:
            return self._perform(action, state)
        except Exception as exc:  # noqa: BLE001 - never let the loop crash on one bad action
            return PerformResult(ok=False, note=f"action failed: {exc}", new_url=self.page.url)

    def _perform(self, action: Action, state: PageState) -> PerformResult:
        if action.type in ("click", "type_text"):
            el = _element(state, action.element)
            if el is None:
                return PerformResult(False, f"invalid element index: {action.element}", self.page.url)
            cx, cy = el.x + el.w // 2, el.y + el.h // 2
            self.page.mouse.move(cx, cy)
            if action.type == "type_text":
                # triple-click selects the field's existing text: typing replaces it
                self.page.mouse.click(cx, cy, click_count=3)
                self.page.keyboard.type(action.text or "")
                return PerformResult(True, f"typed「{action.text}」into「{el.label}」", self.page.url)
            self.page.mouse.click(cx, cy)
            self.page.wait_for_timeout(250)
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            new_url = self.page.url
            moved = new_url != state.url
            return PerformResult(True, f"clicked「{el.label}」" + (f" → {new_url}" if moved else " (same page)"), new_url)

        if action.type == "scroll":
            dy = 480 if action.direction != "up" else -480
            self.page.evaluate(f"window.scrollBy(0, {dy})")
            self.page.wait_for_timeout(200)
            return PerformResult(True, f"scrolled {action.direction or 'down'}", self.page.url)

        if action.type == "back":
            before = self.page.url
            try:
                self.page.go_back(wait_until="domcontentloaded", timeout=8000)
            except Exception:
                pass
            self.page.wait_for_timeout(250)
            if self.page.url == "about:blank":
                # never leave the target site entirely
                try:
                    self.page.go_forward(wait_until="domcontentloaded", timeout=8000)
                except Exception:
                    self.page.goto(before)
                return PerformResult(False, "could not go back (first page)", self.page.url)
            return PerformResult(True, f"went back → {self.page.url}", self.page.url)

        if action.type == "wait":
            time.sleep(min(action.seconds, 10.0) / self.config.speed)
            return PerformResult(True, f"waited {min(action.seconds, 10.0):.1f}s", self.page.url)

        # finish / give_up: nothing to do on the page
        return PerformResult(True, action.note or action.type, self.page.url)

    def close(self) -> None:
        try:
            self.context.close()
        except Exception:
            pass


class PlaywrightBrowserFactory:
    """Shared Chromium process; one fresh context per persona (no cookie bleed)."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._pw = None
        self._browser = None

    def open(self) -> PlaywrightBrowser:
        from playwright.sync_api import sync_playwright

        if self._pw is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.config.headless)
        context = self._browser.new_context(
            viewport={"width": self.config.viewport_w, "height": self.config.viewport_h},
            locale="ja-JP",
        )
        return PlaywrightBrowser(self.config, context)

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            finally:
                self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            finally:
                self._pw = None

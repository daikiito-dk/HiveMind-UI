"""A tiny in-memory browser used by the test suite.

Same interface as PlaywrightBrowser (goto/state/screenshot/perform/close) so the
whole pipeline can run offline: zero network, zero Chromium, zero API keys.
Screenshots are drawn with PIL so recorder/replay code paths are fully exercised.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from .agent import PerformResult
from .schemas import Action, Element, PageState


class FakePage:
    def __init__(
        self,
        key: str,
        title: str,
        text: str,
        elements: list[Element],
        links: dict[int, str] | None = None,
        requires: int | None = None,
        error_text: str | None = None,
    ) -> None:
        self.key = key
        self.title = title
        self.base_text = text
        self.text = text
        self.elements = elements
        self.links = links or {}
        # If set: clicking the `requires` element before any input is filled shows error_text.
        self.requires = requires
        self.error_text = error_text
        self.typed_into: set[int] = set()

    def reset(self) -> None:
        self.text = self.base_text
        self.typed_into = set()


def default_fake_site() -> dict[str, FakePage]:
    def el(i: int, kind: str, label: str, x: int, y: int, w: int, h: int, tag: str = "a") -> Element:
        return Element(index=i, tag=tag, kind=kind, label=label, x=x, y=y, w=w, h=h)  # type: ignore[arg-type]

    return {
        "home": FakePage(
            key="home",
            title="みなと銀行",
            text="みなと銀行へようこそ。各種サービスをご案内いたします。重要なお知らせ：システムメンテナンスのお知らせ。"
            "貯蓄預金キャンペーン実施中。当サイトはInternet Explorer 5.0以上でご覧ください。"
            "あなたは00274番目のお客さまです（1999年1月4日より）。",
            elements=[
                el(0, "link", "他行振込", 40, 90, 90, 20),
                el(1, "link", "残高照会", 150, 90, 90, 20),
                el(2, "link", "口座開設", 260, 90, 90, 20),
                el(3, "link", "ローン", 370, 90, 70, 20),
                el(4, "link", "外貨預金", 450, 90, 90, 20),
                el(5, "link", "重要なお知らせ一覧へ", 400, 300, 260, 26),
                el(6, "link", "閉じる", 640, 180, 24, 14),
            ],
            links={0: "transfer", 5: "info"},
        ),
        "info": FakePage(
            key="info",
            title="重要なお知らせ",
            text="重要なお知らせ一覧。" + "システムメンテナンスは毎月第2日曜日に実施いたします。" * 12,
            elements=[el(0, "link", "ホームへ戻る", 40, 420, 140, 20)],
            links={0: "home"},
        ),
        "transfer": FakePage(
            key="transfer",
            title="他行振込",
            text="お振込金額およびお振込先をご入力のうえ、確認画面へお進みください。",
            elements=[
                el(0, "input", "金額", 60, 200, 200, 26, tag="input"),
                el(1, "input", "振込先口座番号", 60, 240, 200, 26, tag="input"),
                el(2, "button", "確認画面へ", 60, 300, 160, 30, tag="button"),
            ],
            links={2: "confirm"},
            requires=2,
            error_text="入力に誤りがあります。金額をご入力ください。",
        ),
        "confirm": FakePage(
            key="confirm",
            title="振込内容の確認",
            text="お振込内容をご確認ください。この内容で実行する場合は下のボタンを押してください。",
            elements=[
                el(0, "button", "この内容で実行する", 60, 300, 220, 32, tag="button"),
                el(1, "button", "訂正", 320, 300, 90, 32, tag="button"),
            ],
            links={0: "done", 1: "transfer"},
        ),
        "done": FakePage(
            key="done",
            title="取引完了",
            text="取引完了いたしました。受引番号: 1234567890。お取引ありがとうございました。",
            elements=[],
        ),
    }


class FakeBrowser:
    BASE = "file://fake-site/"

    def __init__(self, site: dict[str, FakePage], viewport_w: int = 1280, viewport_h: int = 800) -> None:
        self.site = site
        self.page: FakePage = site["home"]
        self.history: list[str] = ["home"]
        self.w = viewport_w
        self.h = viewport_h
        self._img_cache: dict[str, bytes] = {}

    # ------------------------------------------------------------- protocol
    def goto(self, url: str) -> None:
        key = url.removeprefix(self.BASE) or "home"
        if key not in self.site:
            key = "home"
        self.page = self.site[key]
        self.history = [key]

    def state(self) -> PageState:
        return PageState(
            url=self.BASE + self.page.key,
            title=self.page.title,
            elements=self.page.elements,
            text_digest=self.page.text[:1500],
            viewport_w=self.w,
            viewport_h=self.h,
        )

    def screenshot(self) -> bytes:
        key = self.page.key
        if key not in self._img_cache:
            img = Image.new("RGB", (self.w, self.h), "#f4f2ec")
            draw = ImageDraw.Draw(img)
            draw.text((30, 30), f"HiveMind sim - {self.page.title}", fill="#333333")
            for e in self.page.elements:
                draw.rectangle((e.x, e.y, e.x + e.w, e.y + e.h), outline="#3355aa", width=2)
                draw.text((e.x + 4, e.y + e.h + 2), f"[{e.index}] {e.label[:40]}", fill="#3355aa")
            draw.text((30, self.h - 60), self.page.text[:90], fill="#666666")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self._img_cache[key] = buf.getvalue()
        return self._img_cache[key]

    def perform(self, action: Action, state: PageState) -> PerformResult:
        page = self.page
        if action.type in ("click", "type_text"):
            if action.element is None or not (0 <= action.element < len(page.elements)):
                return PerformResult(False, f"invalid element index: {action.element}", self.BASE + page.key)
            element = page.elements[action.element]
            if action.type == "type_text":
                page.typed_into.add(action.element)
                return PerformResult(True, f"typed「{action.text}」into「{element.label}」", self.BASE + page.key)
            if element.kind == "input":
                return PerformResult(False, "clicked an input field (nothing happened)", self.BASE + page.key)
            if page.requires is not None and action.element == page.requires and not page.typed_into:
                page.text = page.base_text + " ※ " + (page.error_text or "")
                return PerformResult(
                    False, f"clicked「{element.label}」 → error: {page.error_text}", self.BASE + page.key
                )
            target = page.links.get(action.element)
            if target is not None:
                self.page = self.site[target]
                self.history.append(target)
                return PerformResult(True, f"clicked「{element.label}」 → {self.BASE + target}", self.BASE + target)
            return PerformResult(True, f"clicked「{element.label}」 (same page)", self.BASE + page.key)

        if action.type == "back":
            if len(self.history) > 1:
                self.history.pop()
                self.page = self.site[self.history[-1]]
            return PerformResult(True, f"went back → {self.BASE + self.page.key}", self.BASE + self.page.key)

        if action.type == "wait":
            return PerformResult(True, f"waited {min(action.seconds, 10.0):.1f}s", self.BASE + page.key)

        return PerformResult(True, action.note or action.type, self.BASE + page.key)

    def close(self) -> None:
        pass


class FakeBrowserFactory:
    """Each open() builds a FRESH site so page mutations never leak between sessions."""

    def __init__(
        self,
        site_factory=None,
        viewport_w: int = 1280,
        viewport_h: int = 800,
    ) -> None:
        self.site_factory = site_factory or default_fake_site
        self.viewport = (viewport_w, viewport_h)

    def open(self) -> FakeBrowser:
        site = self.site_factory() if callable(self.site_factory) else self.site_factory
        for page in site.values():
            page.reset()
        return FakeBrowser(site, *self.viewport)

    def close(self) -> None:
        pass

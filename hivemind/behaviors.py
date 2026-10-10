"""Persona-conditioned offline behavior model - the mock "brain" of Phase 2.

Every persona trait maps to concrete behavior:
- it_literacy  -> probability of misreading the UI (clicking decoys), hesitation
- patience     -> how much accumulated frustration before the rage-quit
- anxiety      -> hesitation, "wait and re-read" actions, second-guessing
- decisiveness -> hesitation
- attention_to_detail -> occasional pauses to re-read fine print

This is deliberately transparent, seeded and testable, so the offline demo is
reproducible and CI can assert that a low-patience persona gives up earlier.
"""

from __future__ import annotations

import random
import re

from .schemas import Action, Element, PageState, Persona, StepRecord

CLICK_FLOOR = 1.0       # a candidate must score at least one keyword hit

# Controls that submit or advance a form. After typing into a field, a human
# presses the button belonging to that form before wandering off.
SUBMIT_PATTERN = re.compile(
    r"送信|実行|確認|次へ|進む|決定|申込|登録する|ログイン|サインイン|"
    r"submit|continue|confirm|next|sign ?in|log ?in",
    re.IGNORECASE,
)

EXPLORE_THOUGHTS_JA = {
    "scroll": ["下のほうにあるかもしれない。スクロールしてみよう。", "ページの続きは下のはず。下へ。", "もう少し下を見てみよう。"],
    "back": ["ここにはないな。前の画面に戻ってみよう。", "違う画面だった。戻ろう。"],
    "wait": ["まだ読み込み中？", "遅くない？ちょっと待ってみよう。"],
    "give_up": ["もう無理。このサイト、ユーザーのこと何も考えてない。", "時間の無駄。やめた。", "全然分からない。やめる。"],
}
EXPLORE_THOUGHTS_EN = {
    "scroll": ["Might be further down. Scrolling.", "Let's look lower."],
    "back": ["Not here. Going back.", "Wrong screen. Back."],
    "wait": ["Still loading?", "Slow... wait a bit."],
    "give_up": ["That's it. I'm out. Whoever built this hates users.", "Done wasting my time.", "No clue what's going on. Leaving."],
}

COMPLETION_MARKERS = [
    "取引完了", "完了しました", "お手続きが完了", "受付番号", "transaction complete",
    "thank you for your order", "success", "登録が完了",
]

GOAL_SYNONYMS: dict[str, list[str]] = {
    "振込": ["振込", "振り込", "振込み", "送金", "transfer", "remittance"],
    "残高": ["残高", "照会", "明細", "balance"],
    "ログイン": ["ログイン", "サインイン", "login", "signin"],
    "登録": ["登録", "新規", "口座開設", "開設", "signup", "sign up"],
    "完了": ["実行", "確定", "送信", "submit", "confirm", "次へ", "進む", "確認"],
}

DECOY_PATTERN = re.compile(r"戻る|訂正|取消|規約|ヘルプ|一覧|閉じる|戻し|取消|クリア|リンク集", re.IGNORECASE)

# Controls that move the task forward (実行 / 確認 / 次へ / submit...) outrank links
# that merely mention the topic (残高 / 振込). Without this weighting, a topic nav link
# present on every page outscores the form's own "confirm" button, and the persona
# ping-pongs between the same two nav links forever instead of finishing the task.
PROGRESS_WORDS = frozenset(GOAL_SYNONYMS["完了"])
PROGRESS_BONUS = 2.0

# Vocabulary an ordinary person does not know. Reading these is real friction: for a
# low-literacy persona, "I don't understand this page" is the finding that matters most,
# and modelling only wasted *clicks* would let everyone finish a hostile site.
JARGON_TERMS = (
    "被仕向",
    "仕向",
    "通常払込み",
    "テレグラフ払",
    "至急扱い",
    "被仕向科目",
    "被仕向金融機関",
    "被仕向店番号",
    "本人認証区分",
    "電磁的記録",
    "監督官庁",
    "お取引規定",
    "組戻し",
    "手形交換",
)
JARGON_TERM_PENALTY = 0.30   # frustration per unfamiliar term, scaled by illiteracy
JARGON_PAGE_CAP = 1.0        # one jargon-heavy page cannot alone exhaust the patience
LITERACY_COMFORT = 0.3       # illiteracy below this: vocabulary registers, but does not hurt

THOUGHTS_JA: dict[str, list[str]] = {
    "confident": [
        "よし、「{label}」でいこう。",
        "これは「{label}」だな。手早く済ませよう。",
        "たぶん「{label}」で合ってる。さっさと進めよう。",
    ],
    "curious": [
        "「{label}」…これかな？",
        "{title}のどこに{goal}があるんだろう。「{label}」を見てみよう。",
        "ふむ、「{label}」か。開いてみるしかないな。",
    ],
    "confused": [
        "え、この「{label}」で合ってる…？",
        "「{label}」ってどういう意味？普通の日本語で言ってほしい。",
        "あれ、{goal}はどこ…？この「{label}」じゃない気がする。",
        "文字が小さすぎて押すところがよく見えない。",
    ],
    "anxious": [
        "これを押したら変なことにならない…？",
        "「{label}」で間違ってないよね…？",
        "間違えたらどうしよう。もう一度確認しよう。",
    ],
    "frustrated": [
        "まだ終わらないの？「{label}」は違ったし。",
        "なんだこの画面。{goal}はどこにあるんだ。",
        "いちいち分かりにくいんだけど。",
        "さっきと同じ場所に戻ってきた…もう。",
    ],
    "angry": [
        "もう無理。このサイト、ユーザーのこと何も考えてない。",
        "キレそう。この「{label}」、意味不明なんだけど。",
        "時間の無駄。やめた。",
    ],
    "bored": ["まだ読み込み中？", "遅くない？暇だから待つか…"],
    "satisfied": [
        "終わった！意外とすんなりいった。",
        "ふう、これで完了。よし。",
    ],
    "neutral": ["とりあえず「{label}」を見てみよう。", "次は…これかな。"],
}

THOUGHTS_EN: dict[str, list[str]] = {
    "confident": ["Alright, hitting '{label}'.", "That's the one. Let's move."],
    "curious": ["Hmm, '{label}'... maybe?", "Where would {goal} be... let's try '{label}'."],
    "confused": ["Wait, is '{label}' even the right button?", "This label makes no sense to me.", "The text is too small to read."],
    "anxious": ["What if clicking this breaks something?", "Is '{label}' really safe to press...?"],
    "frustrated": ["Still not done? This UI is a maze.", "Why is this so hard to find."],
    "angry": ["That's it. I'm out. Whoever built this hates users.", "I'm done wasting time here."],
    "bored": ["Still loading?", "Slow site... waiting."],
    "satisfied": ["Done! Easier than I feared.", "Okay, finally complete."],
    "neutral": ["Let's check '{label}'.", "Next... probably this."],
}

INPUT_GOAL_WORDS = re.compile(r"金額|宛先|振込|口座|銀行|名義|振替|amount|payee", re.IGNORECASE)

INPUT_VALUES = [
    (re.compile(r"金額|amount", re.I), "30000"),
    (re.compile(r"暗証|pass|pin|pw", re.I), "1234"),
    (re.compile(r"名前|氏名|name", re.I), "{name}"),
    (re.compile(r"mail|メール", re.I), "test@example.com"),
    (re.compile(r"口座|番号|account|宛先", re.I), "1234567"),
]


def goal_keywords(goal: str) -> set[str]:
    """Words that mark progress toward the goal. Progress words (確認/実行/送信...)
    are always included: a UI-test persona is always trying to move forward."""
    words: set[str] = set(GOAL_SYNONYMS["完了"])
    for canon, syns in GOAL_SYNONYMS.items():
        if canon in goal or any(s in goal for s in syns):
            words.update(syns)
    for chunk in re.split(r"[、。,.、!!\s()（）「」・;；:：]+", goal):
        if len(chunk) >= 2:
            words.add(chunk)
    return words


class MockActionPolicy:
    """Decides the next Action for a persona, deterministically for a given seed."""

    def __init__(self, persona: Persona, task: str, seed: int | None = None) -> None:
        self.persona = persona
        self.task = task
        self.rng = random.Random(seed if seed is not None else 0)
        self.frustration = 0.0
        self.failed_targets: set[tuple[str, int]] = set()
        self.scrolls: dict[str, int] = {}
        self.waited_urls: set[str] = set()
        # Consecutive clicks that got the persona nowhere. This is the machine
        # equivalent of the give-up conditions Phase 1 writes into every persona
        # ("cannot find the next button for 3 steps") and guarantees the session
        # ends in a decision rather than by exhausting the step budget.
        self.wasted_streak = 0
        self.exploration_streak = 0
        self.visited: dict[str, int] = {}
        self.rage_pending = 0
        self.rage_target: int | None = None
        self.typed: set[tuple[str, int]] = set()
        # "I just filled a field on this page" -> next move is that form's button.
        self.pending_submit_url: str | None = None
        self.last_field_y: int = 0
        self.submit_scrolls = 0
        # Jargon already read, per page, so the same wall of terms is not re-charged.
        self.seen_jargon: set[tuple[str, str]] = set()
        self._keywords = goal_keywords(task)

    # ------------------------------------------------------------------ main
    def decide(self, persona: Persona, state: PageState, steps: list[StepRecord], shot: bytes | None = None) -> Action:
        self.persona = persona
        self.visited[state.url] = max(self.visited.get(state.url, 0), 1)
        self._update_frustration(state, steps)
        self._read_load(state)

        if self._is_complete(state):
            return self._finish(state)

        if self.rage_pending > 0:
            return self._rage_click(state, steps)

        patience = persona.personality.patience
        # Frustration is charged ~0.3 per dead click, so the threshold is scaled to be
        # reachable inside a session: an average user leaves after roughly a dozen
        # dead ends, a saint after roughly twice that.
        threshold = 2.0 + 5.0 * patience
        if self.frustration >= threshold:
            return self._start_rage(state, steps)

        # Backstop so a session always ends in a decision rather than by exhausting
        # the step budget. Generous, because trying each decoy once is exploration,
        # not churn.
        if self.wasted_streak >= 6 + int(8 * patience):
            return self._rage_or_give_up(state, steps)

        # Short-term intent beats long-term search: someone who just typed into a
        # form presses that form's button. Without this, personas fill in fields
        # and then wander off without ever submitting anything.
        submit = self._submit_candidate(state)
        if submit is not None:
            self.exploration_streak = 0
            return self._click(persona, state, steps, submit, None, 2.0)

        best, score, second = self._rank_candidates(state)
        action = self._maybe_type(persona, state, best, score)
        if action is not None:
            self.exploration_streak = 0
            return action

        # Form filled but its button is not on screen (buried below a long notice
        # or an intentional spacer): people scroll to hunt for it.
        hunting = self._submit_search(state)
        if hunting is not None:
            self.exploration_streak = 0
            return hunting

        if best is not None and score >= CLICK_FLOOR:
            self.exploration_streak = 0
            return self._click(persona, state, steps, best, second, score)

        return self._explore(persona, state, steps)

    # -------------------------------------------------------------- helpers
    def _is_complete(self, state: PageState) -> bool:
        text = state.text_digest
        return any(marker in text for marker in COMPLETION_MARKERS)

    def _thought(self, emotion: str, label: str = "", title: str = "") -> str:
        pool = THOUGHTS_EN if self.persona.language == "en" else THOUGHTS_JA
        template = self.rng.choice(pool.get(emotion) or pool["neutral"])
        return template.format(label=label[:30], title=title[:30], goal=self.task[:40])

    def _update_frustration(self, state: PageState, steps: list[StepRecord]) -> None:
        if not steps:
            return
        last = steps[-1]
        if last.action.type in ("give_up", "finish"):
            return
        if last.action.type != "click" or last.action.element is None:
            return  # typing has no navigation outcome to judge

        # Label-keyed, not index-keyed: element indexes shift when the page scrolls.
        label = last.target_label or f"#{last.action.element}"
        if last.target_label and SUBMIT_PATTERN.search(last.target_label):
            # Pressed the form's button: the "fill this form" intent is spent,
            # whether or not the site accepted it.
            self.pending_submit_url = None

        moved = bool(last.url_after) and last.url_after != last.url
        if not moved:
            # A click that changes nothing is a dead end on this page.
            self.failed_targets.add((last.url, label))
            self.frustration += 0.32
            self.wasted_streak += 1
            return

        if self.visited.get(last.url_after, 0) > 0:
            # Landing on a page we have already seen is a loop. Record the dead end
            # on BOTH pages: the click starts on one page and lands on the other, so
            # recording only the landing page lets two nav links ping-pong forever.
            self.frustration += 0.34
            self.failed_targets.add((last.url, label))
            self.failed_targets.add((last.url_after, label))
            self.wasted_streak += 1
        else:
            self.frustration = max(0.0, self.frustration - 0.12)
            self.wasted_streak = 0
        self.visited[last.url_after] = self.visited.get(last.url_after, 0) + 1
        self.submit_scrolls = 0

    def _read_load(self, state: PageState) -> None:
        """Cost of reading this page, in frustration.

        A page covered in banking jargon is the single most common reason a
        low-literacy persona abandons the task, so comprehension is charged per
        unfamiliar term, scaled by illiteracy, capped per page, and never
        re-charged for terms already read on that page.
        """
        p = self.persona.personality
        unfamiliar = [
            term
            for term in JARGON_TERMS
            if term in state.text_digest and (state.url, term) not in self.seen_jargon
        ]
        if not unfamiliar:
            return
        for term in unfamiliar:
            self.seen_jargon.add((state.url, term))

        illiteracy = 1.0 - p.it_literacy
        if illiteracy <= LITERACY_COMFORT:
            # At ease with the vocabulary: it registers as friction, but barely.
            self.frustration += 0.05 * len(unfamiliar)
            return
        self.frustration += min(JARGON_PAGE_CAP, JARGON_TERM_PENALTY * illiteracy * len(unfamiliar))

    def _submit_candidate(self, state: PageState) -> Element | None:
        """The control belonging to the form this persona just filled in.

        A human who types into a field presses the button belonging to that form,
        chosen by proximity instead of score, so that header/nav links can never
        outbid the form's own submit button.
        """
        if self.pending_submit_url != state.url or not self.last_field_y:
            return None
        y0 = self.last_field_y

        # Still fields of this form left to fill (above/below the one just typed)?
        # Then keep filling: people complete the form before pressing the button.
        untyped_near = [
            el
            for el in state.elements
            if el.kind == "input"
            and el.tag != "select"
            and (state.url, el.label) not in self.typed
            and y0 - 80 <= el.y <= y0 + 360
        ]
        if untyped_near:
            return None

        nearby = [
            el
            for el in state.elements
            if el.kind != "input"
            and (state.url, el.label) not in self.failed_targets
            and SUBMIT_PATTERN.search(el.label)
            and y0 - 80 <= el.y <= y0 + 360
        ]
        if not nearby:
            return None
        return min(nearby, key=lambda el: abs(el.y - y0))

    def _scroll_room(self, state: PageState) -> bool:
        """Whether this page still has somewhere new to scroll.

        Counted per URL: a page the persona has already scrolled to the bottom of is
        exhausted for good, so revisiting it cannot restart the hunt.
        """
        return self.scrolls.get(state.url, 0) < 3

    def _note_scroll(self, state: PageState) -> None:
        self.scrolls[state.url] = self.scrolls.get(state.url, 0) + 1

    def _submit_search(self, state: PageState) -> Action | None:
        """Scroll toward the button of a filled form that is not on screen yet.

        Bounded per page: after a few scans the persona gives up on the form and
        returns to ordinary exploration, so a form whose button cannot be reached
        cannot trap them in an endless scroll.
        """
        if self.pending_submit_url != state.url or not self.last_field_y:
            return None
        if self.submit_scrolls >= 3 or not self._scroll_room(state):
            self.pending_submit_url = None
            return None
        self.submit_scrolls += 1
        self._note_scroll(state)
        return Action(
            type="scroll",
            direction="down",
            thought="入力したのに、次に進むボタンが見当たらない。下にあるはず。",
            emotion="confused" if self.frustration < 3 else "frustrated",
            hesitate_ms=int(600 + 900 * (1 - self.persona.personality.it_literacy)),
        )

    def _rank_candidates(self, state: PageState) -> tuple[Element | None, float, Element | None]:
        scored: list[tuple[float, Element]] = []
        for el in state.elements:
            if el.kind == "input":
                continue
            if (state.url, el.label) in self.failed_targets:
                continue
            label = el.label.lower()
            hits = sum(label.count(kw.lower()) for kw in self._keywords)
            forwards = sum(label.count(kw.lower()) for kw in self._keywords & PROGRESS_WORDS)
            score = (
                hits * 2.0
                + forwards * PROGRESS_BONUS
                + (0.6 if el.kind == "button" else 0.0)
                + min(0.4, el.w * el.h / 1_000_000)
            )
            # Note: no blanket penalty for header/nav chrome. Penalising every nav
            # element once the nav had been used made legitimate goal paths
            # permanently unreachable (a nav link can be the ONLY route to the goal).
            # Wasted moves are penalised precisely instead: see failed_targets and
            # the loop detection in _update_frustration.
            scored.append((score, el))
        if not scored:
            return None, 0.0, None
        scored.sort(key=lambda pair: pair[0], reverse=True)
        best_score, best = scored[0]
        second = scored[1][1] if len(scored) > 1 else None
        return best, best_score, second

    def _maybe_type(self, persona: Persona, state: PageState, best: Element | None, best_score: float) -> Action | None:
        # Real people do not keyboard-type into dropdowns; text fields only.
        inputs = [
            el for el in state.elements
            if el.kind == "input" and el.tag != "select" and (state.url, el.label) not in self.typed
        ]
        if not inputs:
            return None
        p = persona.personality
        relevant = [el for el in inputs if INPUT_GOAL_WORDS.search(el.label)]
        mid_form = bool(self.last_field_y) and self.pending_submit_url == state.url
        if mid_form:
            # Still filling the form this persona started: take the field nearest
            # the one they just typed, the way someone works down a form.
            nearest = min(inputs, key=lambda el: abs(el.y - self.last_field_y))
            chosen = relevant[0] if relevant else nearest
        elif relevant:
            # A goal-relevant form field (amount, payee...): fill it before moving on.
            chosen = relevant[0]
        elif len(inputs) >= 2 and (p.anxiety > 0.5 or p.it_literacy < 0.4):
            # Cautious and low-literacy users head for the obvious official-looking
            # form (customer number + PIN) instead of hunting through the menu.
            chosen = inputs[0]
        elif best is not None and best_score >= 2.0:
            # A clear navigation target exists; don't fiddle with unrelated forms.
            return None
        else:
            # Lost on a page with a form -> some people start typing anyway.
            chosen = inputs[0]
        if self.rng.random() > 0.35 + 0.6 * p.it_literacy:
            return None  # low literacy sometimes avoids the form (and may click prematurely)
        value = "テスト" if persona.language != "en" else "test"
        for pattern, template in INPUT_VALUES:
            if pattern.search(chosen.label):
                value = template.format(name=persona.name) if "{name}" in template else template
                break
        self.typed.add((state.url, chosen.label))
        # This page now holds a filled field. Remember where it is, so the next
        # decision can reach for the nearby submit control instead of the nav.
        self.pending_submit_url = state.url
        self.last_field_y = chosen.y + chosen.h // 2
        self.submit_scrolls = 0
        hesitate = int(400 + 2600 * (1 - p.it_literacy) + 900 * p.anxiety)
        return Action(
            type="type_text",
            element=chosen.index,
            text=value,
            thought=self._thought("neutral" if hesitate < 1500 else "anxious", label=chosen.label),
            emotion="neutral" if hesitate < 1500 else "anxious",
            hesitate_ms=hesitate,
        )

    def _click(
        self,
        persona: Persona,
        state: PageState,
        steps: list[StepRecord],
        best: Element,
        second: Element | None,
        score: float,
    ) -> Action:
        p = persona.personality
        misfire = False
        target = best
        pm = 0.55 * (1 - p.it_literacy) ** 1.4
        if self.rng.random() < pm:
            decoys = [
                el for el in state.elements
                if el.index != best.index and el.kind != "input" and (DECOY_PATTERN.search(el.label) or el.w * el.h < 2500)
            ]
            decoys = [el for el in decoys if (state.url, el.label) not in self.failed_targets] or decoys
            if decoys:
                target = self.rng.choice(decoys)
                misfire = True
            elif second is not None:
                target = second

        hesitation = 250 + self.rng.uniform(0, 1) * (2400 * (1 - p.it_literacy) + 1400 * p.anxiety + 900 * (1 - p.decisiveness))
        if misfire:
            hesitation += 1400

        emotion = "confident" if score >= 1.5 and p.it_literacy > 0.5 else "curious"
        if misfire:
            emotion = "anxious" if p.anxiety > 0.5 else "confused"

        threshold = 2.0 + 5.0 * p.patience
        if self.frustration > threshold * 0.85:
            emotion = "angry"
        elif self.frustration > threshold * 0.6:
            emotion = "frustrated"

        if self.rng.random() < p.attention_to_detail * 0.12:
            return Action(
                type="wait",
                seconds=1.5,
                thought="ちょっと待って、この内容をもう一度確認させて…",
                emotion="anxious" if p.anxiety > 0.5 else "neutral",
                hesitate_ms=600,
            )

        return Action(
            type="click",
            element=target.index,
            thought=self._thought(emotion, label=target.label, title=state.title),
            emotion=emotion,
            hesitate_ms=int(hesitation),
        )

    def _rage_or_give_up(self, state: PageState, steps: list[StepRecord]) -> Action:
        """Leave the site: furious first, then out the door.

        A persona who still has feeling left in them takes it out on the UI with a
        short burst of pointless clicks; the session ends with give_up either way.
        """
        if self.frustration >= 1.0 and any(el.kind != "input" for el in state.elements):
            return self._start_rage(state, steps)
        pool_gu = EXPLORE_THOUGHTS_EN if self.persona.language == "en" else EXPLORE_THOUGHTS_JA
        reason = (
            self.persona.give_up_conditions[0] if self.persona.give_up_conditions else "目的の場所が見つからない"
        )
        return Action(
            type="give_up",
            note=reason,
            thought=self.rng.choice(pool_gu["give_up"]),
            emotion="angry",
            hesitate_ms=0,
        )

    def _explore(self, persona: Persona, state: PageState, steps: list[StepRecord]) -> Action:
        # Honoring the persona's documented give-up condition: N consecutive steps
        # with no idea where the goal is -> bounce. Impatient people leave sooner.
        needed = 3 + int(6 * persona.personality.patience)
        self.exploration_streak += 1
        if self.exploration_streak >= needed:
            return self._rage_or_give_up(state, steps)
        self.frustration += 0.25
        pool = EXPLORE_THOUGHTS_EN if persona.language == "en" else EXPLORE_THOUGHTS_JA
        urls_seen = [s.url for s in steps]
        can_go_back = len(set(urls_seen)) > 1 and (state.url, "back") not in self.failed_targets
        long_page = len(state.text_digest) > 500
        if long_page and self._scroll_room(state) and self.rng.random() < 0.8:
            self._note_scroll(state)
            return Action(
                type="scroll",
                direction="down",
                thought=self.rng.choice(pool["scroll"]),
                emotion="curious" if self.frustration < 2 else "frustrated",
                hesitate_ms=int(700 + 1200 * (1 - persona.personality.it_literacy)),
            )
        if can_go_back:
            self.failed_targets.add((state.url, "back"))
            return Action(
                type="back",
                thought=self.rng.choice(pool["back"]),
                emotion="confused" if self.frustration < 4 else "frustrated",
                hesitate_ms=900,
            )
        if state.url not in self.waited_urls:
            # Nothing left to scroll and nowhere to go back to. A real person waits
            # once, in case the page is still loading, and then decides.
            self.waited_urls.add(state.url)
            return Action(
                type="wait", seconds=2.0, thought=self.rng.choice(pool["wait"]), emotion="bored", hesitate_ms=400
            )
        # Genuinely out of options on this page: leaving is a decision, not churn.
        # Without this the session burns its whole step budget and reports a timeout.
        return self._rage_or_give_up(state, steps)

    # ---------------------------------------------------------------- rage
    def _start_rage(self, state: PageState, steps: list[StepRecord]) -> Action:
        last_target = None
        for s in reversed(steps):
            if s.action.type == "click" and s.action.element is not None:
                last_target = s.action.element
                break
        if last_target is None:
            last_target = state.elements[0].index if state.elements else None
        self.rage_target = last_target
        self.rage_pending = 2
        return Action(
            type="click",
            element=last_target,
            thought=self._thought("angry", label=self._label_of(state, last_target)),
            emotion="angry",
            hesitate_ms=0,
        )

    def _rage_click(self, state: PageState, steps: list[StepRecord]) -> Action:
        self.rage_pending -= 1
        if self.rage_pending > 0:
            return Action(
                type="click",
                element=self.rage_target,
                thought=self._thought("angry", label=self._label_of(state, self.rage_target)),
                emotion="angry",
                hesitate_ms=0,
            )
        pool_gu = EXPLORE_THOUGHTS_EN if self.persona.language == "en" else EXPLORE_THOUGHTS_JA
        reason = self.persona.give_up_conditions[0] if self.persona.give_up_conditions else self._thought("angry")
        return Action(
            type="give_up",
            note=reason,
            thought=self.rng.choice(pool_gu["give_up"]),
            emotion="angry",
            hesitate_ms=0,
        )

    def _label_of(self, state: PageState, index: int | None) -> str:
        if index is None:
            return ""
        for el in state.elements:
            if el.index == index:
                return el.label
        return ""

    def _finish(self, state: PageState) -> Action:
        return Action(
            type="finish",
            note=f"{self.task[:60]} - 完了した。",
            thought=self._thought("satisfied", title=state.title),
            emotion="satisfied",
            hesitate_ms=300,
        )

"""Phase 2 behavior-model tests (offline, deterministic)."""

from hivemind.behaviors import MockActionPolicy, goal_keywords
from hivemind.fake_browser import FakeBrowser, default_fake_site
from hivemind.schemas import Action, PageState, Persona, PersonaTraits, StepRecord

TASK = "他行あてに3万円を振り込む"


def make_persona(literacy: float, patience: float) -> Persona:
    return Persona(
        id="p-test",
        archetype="テスト用",
        name="テスト太郎",
        age=40,
        language="ja",
        personality=PersonaTraits(
            it_literacy=literacy, patience=patience, decisiveness=0.6,
            attention_to_detail=0.5, anxiety=0.3,
        ),
        goal=TASK,
        give_up_conditions=["3回連続で目的のボタンが見つからない"],
    )


def simulate(persona: Persona, seed: int, max_steps: int = 40):
    browser = FakeBrowser(default_fake_site())
    policy = MockActionPolicy(persona, TASK, seed=seed)
    steps: list[StepRecord] = []
    browser.goto("https://example.com")
    action = None
    for i in range(max_steps):
        state = browser.state()
        action = policy.decide(persona, state, steps)
        perform = browser.perform(action, state)
        steps.append(
            StepRecord(
                step=i + 1, t_ms=(i + 1) * 1000, action=action, url=state.url,
                url_after=perform.new_url, result_ok=perform.ok, result_note=perform.note,
            )
        )
        if action.type in ("finish", "give_up"):
            break
    return action, steps


def test_goal_keywords():
    words = goal_keywords(TASK)
    assert "振込" in words
    # progress words are always candidates
    assert {"実行", "確認", "送信"} <= words


def test_high_literacy_patient_persona_completes():
    persona = make_persona(literacy=0.85, patience=0.7)
    action, steps = simulate(persona, seed=11)
    assert action is not None and action.type == "finish", f"steps={[s.action.type for s in steps]}"
    urls = [s.url_after for s in steps]
    assert any("#transfer" in u or "transfer" in u for u in urls if u), urls
    assert any(s.action.type == "type_text" for s in steps)


def test_patience_and_literacy_drive_outcomes():
    """Across seeds, on the same trap-rich site: low literacy + impatience
    produces drop-offs that competent patient personas never hit."""
    low: list[str] = []
    mid: list[str] = []
    high: list[str] = []
    for seed in range(10):
        low.append(simulate(make_persona(literacy=0.1, patience=0.1), seed=seed)[0].type)
        mid.append(simulate(make_persona(literacy=0.5, patience=0.4), seed=seed)[0].type)
        high.append(simulate(make_persona(literacy=0.85, patience=0.7), seed=seed)[0].type)
    assert low.count("give_up") > high.count("give_up")
    assert low.count("give_up") >= mid.count("give_up")
    assert high.count("finish") == 10


def test_deterministic_simulation():
    persona = make_persona(literacy=0.4, patience=0.35)
    a, steps_a = simulate(persona, seed=7)
    b, steps_b = simulate(persona, seed=7)
    assert [s.action.type for s in steps_a] == [s.action.type for s in steps_b]
    assert [s.action.emotion for s in steps_a] == [s.action.emotion for s in steps_b]


def test_jargon_friction_scales_with_illiteracy():
    """A wall of banking jargon costs a low-literacy persona more than a confident one,
    and reading the same wall twice is not charged twice."""
    page = PageState(
        url="https://example.com/transfer",
        title="他行振込",
        text_digest="被仕向金融機関および被仕向店番号をご入力ください。通常払込みはテレグラフ払と異なります。",
    )
    low_persona = make_persona(literacy=0.15, patience=0.5)
    high_persona = make_persona(literacy=0.85, patience=0.5)
    low = MockActionPolicy(low_persona, TASK, seed=0)
    high = MockActionPolicy(high_persona, TASK, seed=0)
    low.persona, high.persona = low_persona, high_persona

    low._read_load(page)
    high._read_load(page)
    assert low.frustration > high.frustration, "jargon must hurt the low-literacy persona more"

    charged = low.frustration
    low._read_load(page)
    assert low.frustration == charged, "the same page's terms must not be re-charged"


def test_dead_end_is_recorded_on_both_pages():
    """A click that ping-pongs between two pages must be struck off on the page it
    started from too, otherwise the model loops between the same two links forever."""
    persona = make_persona(literacy=0.6, patience=0.5)
    policy = MockActionPolicy(persona, TASK, seed=0)
    # both pages already visited, so this click is a loop
    policy.visited = {"https://x.test/a": 1, "https://x.test/b": 1}
    loop_step = StepRecord(
        step=1,
        t_ms=0,
        action=Action(type="click", element=1, thought="", emotion="neutral"),
        url="https://x.test/a",
        target_label="残高照会",
        url_after="https://x.test/b",
    )
    policy._update_frustration(PageState(url="https://x.test/b"), [loop_step])

    assert ("https://x.test/a", "残高照会") in policy.failed_targets
    assert ("https://x.test/b", "残高照会") in policy.failed_targets


def test_click_that_changes_nothing_is_a_dead_end():
    persona = make_persona(literacy=0.6, patience=0.5)
    policy = MockActionPolicy(persona, TASK, seed=0)
    stuck = StepRecord(
        step=1,
        t_ms=0,
        action=Action(type="click", element=4, thought="", emotion="neutral"),
        url="https://x.test/home",
        target_label="サイトマップ",
        url_after="https://x.test/home",
    )
    policy._update_frustration(PageState(url="https://x.test/home"), [stuck])

    assert ("https://x.test/home", "サイトマップ") in policy.failed_targets
    assert policy.frustration > 0
    assert policy.wasted_streak == 1


def test_filling_a_form_ends_with_pressing_its_button():
    """Someone who types into a form presses that form's button, instead of wandering
    off into the navigation with the form half-finished."""
    persona = make_persona(literacy=0.95, patience=0.9)
    browser = FakeBrowser(default_fake_site())
    policy = MockActionPolicy(persona, TASK, seed=5)
    browser.goto("https://example.com")

    steps: list[StepRecord] = []
    typed = 0
    click_after_typing: str | None = None
    for i in range(14):
        state = browser.state()
        action = policy.decide(persona, state, steps)
        label = None
        if action.element is not None and 0 <= action.element < len(state.elements):
            label = state.elements[action.element].label
        if action.type == "type_text":
            typed += 1
        elif action.type == "click" and typed:
            click_after_typing = label
            break
        perform = browser.perform(action, state)
        steps.append(
            StepRecord(
                step=i + 1,
                t_ms=(i + 1) * 1000,
                action=action,
                url=state.url,
                target_label=label,
                url_after=perform.new_url,
                result_ok=perform.ok,
                result_note=perform.note,
            )
        )

    assert typed > 0, "persona never filled a field"
    assert click_after_typing == "確認画面へ", f"clicked {click_after_typing!r} instead of the form's button"

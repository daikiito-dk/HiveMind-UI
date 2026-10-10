"""Executor mechanics: rage-click counting, action timing, loop termination."""

from hivemind.agent import SessionExecutor, action_sim_ms, count_rage_clicks
from hivemind.behaviors import MockActionPolicy
from hivemind.config import Config
from hivemind.fake_browser import FakeBrowserFactory
from hivemind.recorder import SessionRecorder
from hivemind.schemas import Action, Persona, PersonaTraits, StepRecord

TASK = "他行あてに3万円を振り込む"


def step(n: int, t_ms: int, action: Action) -> StepRecord:
    return StepRecord(step=n, t_ms=t_ms, action=action, url="https://x.test")


def test_count_rage_clicks():
    rage_click = Action(type="click", element=3, thought="無理", emotion="angry")
    calm_click = Action(type="click", element=3, thought="ok", emotion="confident")
    steps = [
        step(1, 1000, Action(type="wait", seconds=1, emotion="neutral")),
        step(2, 2000, calm_click),          # not angry: not counted
        step(3, 2600, rage_click),          # 600ms after same target, angry: counted
        step(4, 3100, rage_click),          # counted again
        step(5, 9000, rage_click),          # too slow: not counted
        step(6, 9500, Action(type="give_up", note="やめた", emotion="angry")),
    ]
    assert count_rage_clicks(steps) == 2


def test_count_rage_clicks_empty():
    assert count_rage_clicks([]) == 0
    assert count_rage_clicks([step(1, 0, Action(type="wait", seconds=1, emotion="neutral"))]) == 0


def test_action_sim_ms():
    assert action_sim_ms(Action(type="click", element=0, thought="", emotion="neutral")) == 550
    assert action_sim_ms(Action(type="type_text", element=0, text="12345", emotion="neutral")) == 400 + 5 * 70
    assert action_sim_ms(Action(type="wait", seconds=999, emotion="neutral")) == 10_000
    assert action_sim_ms(Action(type="back", emotion="neutral")) == 900
    assert action_sim_ms(Action(type="finish", note="done", emotion="satisfied")) == 250


def test_dwell_time_includes_hesitation(tmp_path):
    """A persona cannot have spent less time on the site than they spent thinking.
    Reports quote both numbers side by side, so they must be consistent."""
    persona = Persona(
        id="p1",
        archetype="テスト用",
        name="テスト太郎",
        age=40,
        language="ja",
        personality=PersonaTraits(
            it_literacy=0.4, patience=0.5, decisiveness=0.5, attention_to_detail=0.5, anxiety=0.6
        ),
        goal=TASK,
    )
    config = Config.from_env(mode="mock", runs_dir=tmp_path, seed=3, speed=10_000.0, max_steps=25)
    factory = FakeBrowserFactory()
    browser = factory.open()
    recorder = SessionRecorder(tmp_path / "run", persona)
    try:
        result, steps = SessionExecutor(config, "https://example.com").run(
            persona, MockActionPolicy(persona, TASK, seed=3), browser, recorder
        )
    finally:
        browser.close()
        factory.close()

    assert steps, "the session produced no steps"
    assert result.hesitation_ms > 0
    assert result.sim_ms >= result.hesitation_ms

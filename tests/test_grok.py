"""Grok client unit tests: tool schema, structured action parsing, error paths.

No network access: these tests only exercise offline pieces.
"""

import pytest

from hivemind import runner
from hivemind.config import Config
from hivemind.fake_browser import FakeBrowserFactory
from hivemind.grok import BROWSER_ACTION_TOOL, GrokClient, GrokError
from hivemind.schemas import Action, Persona, PersonaBatch, PersonaTraits


def test_browser_action_tool_schema_is_valid_json_schema():
    schema = BROWSER_ACTION_TOOL["parameters"]
    assert schema["type"] == "object"
    assert "click" in schema["properties"]["type"]["enum"]
    assert "give_up" in schema["properties"]["type"]["enum"]
    # structured-output-safe: no unsupported keywords
    assert "$ref" not in schema


def test_action_roundtrip_through_tool_arguments():
    action = Action(type="click", element=3, thought="これだ", emotion="confident", hesitate_ms=800)
    import json

    parsed = Action.model_validate_json(json.dumps(action.model_dump()))
    assert parsed == action


def test_grok_client_requires_key(tmp_path):
    config = Config.from_env(mode="grok", api_key=None)
    client = GrokClient(config)
    with pytest.raises(GrokError):
        _ = client.client


def test_resolved_mode_auto():
    assert Config.from_env(mode="auto", api_key=None).resolved_mode() == "mock"
    assert Config.from_env(mode="auto", api_key="sk-test").resolved_mode() == "grok"
    assert Config.from_env(mode="mock", api_key="sk-test").resolved_mode() == "mock"


class FakeResponse:
    """Stands in for a ParsedResponse, including its silent-empty failure mode."""

    def __init__(self, parsed: PersonaBatch | None, status: str = "completed", text: str = ""):
        self.output_parsed = parsed
        self.status = status
        self.output_text = text


def make_persona() -> Persona:
    return Persona(
        id="",
        archetype="慎重シニア",
        name="洋子",
        age=68,
        language="ja",
        personality=PersonaTraits(
            it_literacy=0.16, patience=0.3, decisiveness=0.2, attention_to_detail=0.7, anxiety=0.8
        ),
    )


def test_generate_personas_retries_when_the_first_answer_is_empty(monkeypatch):
    """`responses.parse` returns an empty PersonaBatch when it cannot parse the model's
    answer, so an empty result must be retried rather than accepted as a real cohort."""
    client = GrokClient(Config.from_env(mode="grok", api_key="sk-test", search_max_turns=1))
    calls: list[int] = []

    def fake_call(fn):
        calls.append(1)
        if len(calls) == 1:
            return FakeResponse(PersonaBatch())
        return FakeResponse(PersonaBatch(personas=[make_persona()]))

    monkeypatch.setattr(client, "_call", fake_call)
    batch = client.generate_personas("節約志向", "振込", count=1, language="ja")

    assert len(calls) == 2, "the empty answer should have been retried"
    assert len(batch.personas) == 1
    assert batch.personas[0].id == "p1"
    assert batch.personas[0].generated_by == "grok"
    assert batch.personas[0].goal == "振込"


def test_generate_personas_raises_when_still_empty(monkeypatch):
    """An empty cohort must fail loudly. Silently running zero sessions once produced a
    report implying the site could not be tested, which read as a finding."""
    client = GrokClient(Config.from_env(mode="grok", api_key="sk-test", search_max_turns=1))
    monkeypatch.setattr(client, "_call", lambda fn: FakeResponse(PersonaBatch(), text="{}"))

    with pytest.raises(GrokError, match="no personas"):
        client.generate_personas("節約志向", "振込", count=2, language="ja")


def test_run_cohort_refuses_an_empty_persona_list(tmp_path, monkeypatch):
    """Even with a generator that returns nothing, the runner must not write reports
    from a cohort of zero personas."""

    class EmptyGenerator:
        def generate(self, **kwargs):
            return PersonaBatch()

    monkeypatch.setattr(runner, "make_persona_generator", lambda config, grok: EmptyGenerator())
    config = Config.from_env(mode="mock", runs_dir=tmp_path / "runs", speed=10_000.0)

    with pytest.raises(GrokError, match="no personas"):
        runner.run_cohort(
            config,
            target_url="https://example.com",
            task="振込",
            browser_factory=FakeBrowserFactory(),
        )

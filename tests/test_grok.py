"""Grok client unit tests: tool schema, structured action parsing, error paths.

No network access: these tests only exercise offline pieces.
"""

import pytest

from hivemind.config import Config
from hivemind.grok import BROWSER_ACTION_TOOL, GrokClient, GrokError
from hivemind.schemas import Action


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

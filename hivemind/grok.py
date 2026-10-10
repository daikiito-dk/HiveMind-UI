"""Thin xAI (Grok) API wrapper.

Covers the three HiveMind calls:
- Phase 1: persona generation grounded in live X data (X Search tool + structured outputs)
- Phase 2: one browser_action per turn (function calling loop)
- Phase 3: subjective review of a session / a whole cohort (structured outputs)

Docs: https://docs.x.ai  (Responses API, base https://api.x.ai/v1)
"""

from __future__ import annotations

import json
import time
import warnings
from datetime import date, timedelta
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from .config import Config
from .schemas import Action, CohortReport, PersonaBatch, Report

XAI_BASE_URL = "https://api.x.ai/v1"

BROWSER_ACTION_TOOL = {
    "type": "function",
    "name": "browser_action",
    "description": (
        "Perform the next single browser action as the persona. "
        "click: element index from the list. type_text: element index + text to type. "
        "scroll: direction up/down. back: browser back. wait: seconds. "
        "finish: task complete (note = one-sentence verdict in persona voice). "
        "give_up: persona's patience is exhausted (note = last words)."
    ),
    "parameters": Action.model_json_schema(),
}


class GrokError(RuntimeError):
    pass


def _x_search_tool(config: Config) -> dict[str, Any]:
    tool: dict[str, Any] = {"type": "x_search"}
    if config.search_days > 0:
        today = date.today()
        tool["from_date"] = (today - timedelta(days=config.search_days)).isoformat()
        tool["to_date"] = (today + timedelta(days=1)).isoformat()
    return tool


def _action_from_text(text: str) -> Action | None:
    """Recover an Action from a JSON object returned as message text.

    Grok is asked for a tool call, but if it answers with the action as JSON text
    (optionally inside a markdown fence) we still want the session to continue.
    """
    if not text:
        return None
    candidates: list[str] = []
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    start = text.find("{")
    if start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            candidates.append(json.dumps(obj, ensure_ascii=False))
        except ValueError:
            pass
    for candidate in candidates:
        try:
            return Action.model_validate_json(candidate)
        except ValidationError:
            continue
    return None


class GrokClient:
    """Shared client for all Grok calls in one run. Tracks usage for cost transparency."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._client: OpenAI | None = None
        self.usage = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "x_search_calls": 0,
            "x_posts_fetched": 0,
            "cost_ticks": 0,
            "cost_usd": 0.0,
        }

    # ------------------------------------------------------------------ infra
    @property
    def client(self) -> OpenAI:
        if not self.config.api_key:
            raise GrokError("XAI_API_KEY is not set. Run with --mode mock or add a key to .env.")
        if self._client is None:
            self._client = OpenAI(api_key=self.config.api_key, base_url=XAI_BASE_URL)
        return self._client

    def _record_usage(self, resp: Any) -> None:
        """Accumulate the per-request usage xAI reports.

        `cost_in_usd_ticks` is the exact amount billed for the request, including
        server-side tool costs (1 USD = 10^10 ticks, per docs.x.ai/developers/cost-tracking).
        `server_side_tool_usage_details` arrives as a plain dict, not an object.
        """
        self.usage["calls"] += 1
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        self.usage["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
        self.usage["output_tokens"] += getattr(usage, "output_tokens", 0) or 0

        ticks = getattr(usage, "cost_in_usd_ticks", 0) or 0
        self.usage["cost_ticks"] += ticks
        self.usage["cost_usd"] = round(self.usage["cost_ticks"] / 1e10, 6)

        details = getattr(usage, "server_side_tool_usage_details", None) or {}
        if isinstance(details, dict):
            self.usage["x_search_calls"] += details.get("x_search_calls", 0) or 0
            self.usage["x_posts_fetched"] += details.get("x_posts_fetched", 0) or 0
        else:  # tolerate a pydantic-style object
            self.usage["x_search_calls"] += getattr(details, "x_search_calls", 0) or 0
            self.usage["x_posts_fetched"] += getattr(details, "x_posts_fetched", 0) or 0

    def _call(self, fn) -> Any:
        last: Exception | None = None
        for attempt in range(3):
            try:
                with warnings.catch_warnings():
                    # The OpenAI SDK does not model xAI's server-side tools, so it emits
                    # one "serialized value may not be as expected" warning per tool on
                    # every request. The payload is sent correctly; this is just noise.
                    warnings.simplefilter("ignore")
                    resp = fn()
                self._record_usage(resp)
                return resp
            except GrokError:
                raise
            except Exception as exc:  # network / rate limit / transient server errors
                last = exc
                time.sleep(1.5**attempt)
        raise GrokError(f"Grok API call failed after 3 attempts: {last}")

    # ---------------------------------------------------------------- Phase 1
    def generate_personas(self, context: str, task: str, count: int, language: str) -> PersonaBatch:
        """Generate the cohort, grounded in live X posts, and never return nothing.

        `responses.parse` hands back a default-constructed `PersonaBatch` when the model's
        answer cannot be parsed against the schema. That is indistinguishable from a
        successful call that legitimately produced no personas, and it happened in
        practice: a cohort spent 6 X-search rounds and 33 fetched posts, then ran zero
        sessions and wrote a report implying nothing could be tested. So an empty result
        is retried once with a blunter instruction, and then raised.
        """
        from .prompts import persona_system_prompt

        system = persona_system_prompt(task, context, count, language)
        notes: list[str] = []
        for attempt in (1, 2):
            user = f"Research X and generate the {count} personas now."
            if attempt > 1:
                user += (
                    " Do the X research first, then answer with the PersonaBatch JSON only."
                    f" The personas list MUST contain exactly {count} entries."
                )

            def call(u=user):
                return self.client.responses.parse(
                    model=self.config.model,
                    input=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": u},
                    ],
                    tools=[_x_search_tool(self.config)],
                    text_format=PersonaBatch,
                    extra_body={"max_turns": self.config.search_max_turns},
                )

            resp = self._call(call)
            batch = resp.output_parsed
            if batch is not None and batch.personas:
                batch.personas = batch.personas[:count]
                for i, persona in enumerate(batch.personas):
                    persona.id = persona.id or f"p{i + 1}"
                    persona.generated_by = "grok"
                    if not persona.goal:
                        persona.goal = task
                return batch

            text = getattr(resp, "output_text", "") or ""
            notes.append(
                f"attempt {attempt}: status={getattr(resp, 'status', None)!r}, "
                f"personas={len(batch.personas) if batch else None}, output_chars={len(text)}"
            )
        raise GrokError(
            "Phase 1 produced no personas (" + "; ".join(notes) + "). This is a model/parse "
            "failure, not an empty cohort. Retry, or lower HIVEMIND_PERSONAS or "
            "HIVEMIND_SEARCH_MAX_TURNS."
        )

    # ---------------------------------------------------------------- Phase 2
    def decide_action(self, system_prompt: str, brief: str, screenshot_b64: str | None) -> Action:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": brief}]
        if screenshot_b64 and self.config.vision:
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{screenshot_b64}"})

        def call(extra: str = ""):
            return self.client.responses.create(
                model=self.config.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content if not extra else [*content, {"type": "input_text", "text": extra}]},
                ],
                tools=[BROWSER_ACTION_TOOL],
                # Without this, grok-4.7 answers with the action as JSON *text* in a
                # message instead of emitting a function call. Forcing the choice makes
                # the one-action-per-turn contract structural rather than hoped for.
                tool_choice="required",
            )

        for nudge in ("", "Respond with exactly one browser_action tool call."):
            resp = self._call(lambda n=nudge: call(n))
            for item in getattr(resp, "output", []) or []:
                if getattr(item, "type", "") == "function_call" and item.name == "browser_action":
                    return Action.model_validate_json(item.arguments)
            # Defensive: tolerate a JSON action delivered as message text.
            fallback = _action_from_text(getattr(resp, "output_text", "") or "")
            if fallback is not None:
                return fallback
        raise GrokError("Grok returned no browser_action call")

    # ---------------------------------------------------------------- Phase 3
    def review_session(self, system_prompt: str, payload: str, screenshots: list[bytes]) -> Report:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": payload}]
        if self.config.vision:
            import base64

            for png in screenshots[: self.config.analyst_shots]:
                content.append(
                    {"type": "input_image", "image_url": f"data:image/png;base64,{base64.b64encode(png).decode()}"}
                )

        def call():
            return self.client.responses.parse(
                model=self.config.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                text_format=Report,
            )

        report = self._call(call).output_parsed
        report.generated_by = "grok"
        return report

    def review_cohort(self, system_prompt: str, payload: str) -> CohortReport:
        def call():
            return self.client.responses.parse(
                model=self.config.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [{"type": "input_text", "text": payload}]},
                ],
                text_format=CohortReport,
            )

        report = self._call(call).output_parsed
        report.generated_by = "grok"
        return report


def json_compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

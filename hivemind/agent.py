"""Phase 2 - Agentic execution.

SessionExecutor runs ONE persona through the target site, regardless of who is
"driving": the mock behavior model (offline) or Grok in a function-calling loop.
Policies share one interface: decide(persona, state, steps, shot) -> Action.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

from .config import Config
from .grok import GrokClient, GrokError
from .prompts import agent_system_prompt, step_brief
from .schemas import Action, Persona, SessionResult, StepRecord


def action_sim_ms(action: Action) -> int:
    """How long this action takes a human, in simulated ms."""
    if action.type == "click":
        return 550
    if action.type == "type_text":
        return 400 + 70 * len(action.text or "")
    if action.type == "scroll":
        return 650
    if action.type == "back":
        return 900
    if action.type == "wait":
        return int(min(action.seconds, 10.0) * 1000)
    return 250


def count_rage_clicks(steps: list[StepRecord]) -> int:
    """Rapid repeated clicks on the same target while frustrated/angry."""
    rage = 0
    for prev, cur in zip(steps, steps[1:], strict=False):
        if (
            cur.action.type == "click"
            and prev.action.type == "click"
            and cur.action.emotion in ("angry", "frustrated")
            and cur.action.element == prev.action.element
            and cur.t_ms - prev.t_ms <= 1500
        ):
            rage += 1
    return rage


@dataclass
class PerformResult:
    ok: bool
    note: str
    new_url: str | None = None


class GrokActionPolicy:
    """Grok embodies the persona and emits one browser_action per turn."""

    def __init__(self, client: GrokClient, persona: Persona, task: str) -> None:
        self.client = client
        self.persona = persona
        self.system_prompt = agent_system_prompt(persona, task)

    def decide(
        self,
        persona: Persona,
        state,  # PageState
        steps: list[StepRecord],
        shot: bytes | None,
    ) -> Action:
        sim_ms = steps[-1].t_ms if steps else 0
        history = [
            f"step {s.step}: {s.action.type}"
            + (f"({s.result_note})" if s.result_note else "")
            + f" [{s.action.emotion}]"
            for s in steps
        ]
        elements_text = "\n".join(
            f"[{el.index}] {el.kind} | {el.label[:60]} | {el.w}x{el.h} at ({el.x},{el.y})"
            for el in state.elements[:60]
        )
        brief = step_brief(
            step=len(steps) + 1,
            url=state.url,
            title=state.title,
            elements_text=elements_text or "(no interactive elements visible)",
            digest=state.text_digest[:900] or "(empty page)",
            history=history,
            sim_ms=sim_ms,
        )
        shot_b64 = base64.b64encode(shot).decode() if shot else None
        action = self.client.decide_action(self.system_prompt, brief, shot_b64)
        action.hesitate_ms = min(action.hesitate_ms, 8000)
        return action


class SessionExecutor:
    """Runs one persona session end-to-end and records every step."""

    def __init__(self, config: Config, target_url: str) -> None:
        self.config = config
        self.target_url = target_url

    def run(
        self,
        persona: Persona,
        policy,
        browser,
        recorder,
        progress=None,
    ) -> tuple[SessionResult, list[StepRecord]]:
        wall_start = time.monotonic()
        sim_ms = 0
        hesitation_ms = 0
        steps: list[StepRecord] = []
        outcome = "timeout"
        verdict: str | None = None
        reason: str | None = None

        browser.goto(self.target_url)

        for step in range(1, self.config.max_steps + 1):
            state = browser.state()
            shot_bytes = browser.screenshot()

            try:
                action = policy.decide(persona, state, steps, shot_bytes)
            except GrokError as exc:
                outcome, reason = "error", str(exc)
                break

            fname = recorder.save_shot(step, shot_bytes)
            # Hesitation is capped per step so one pathological pause cannot eat the
            # whole session, and it counts as time spent on the site: dwell time must
            # always be at least the hesitation inside it.
            hesitate_ms = min(action.hesitate_ms, 8000)
            if hesitate_ms:
                time.sleep(hesitate_ms / 1000.0 / self.config.speed)
            hesitation_ms += hesitate_ms

            cursor = None
            target_label = None
            if action.element is not None and 0 <= action.element < len(state.elements):
                el = state.elements[action.element]
                cursor = (el.x + el.w // 2, el.y + el.h // 2)
                target_label = el.label

            perform = browser.perform(action, state)
            sim_ms += action_sim_ms(action) + hesitate_ms

            rec = StepRecord(
                step=step,
                t_ms=sim_ms,
                action=action,
                url=state.url,
                title=state.title,
                shot=fname,
                cursor=cursor,
                target_label=target_label,
                url_after=perform.new_url,
                result_ok=perform.ok,
                result_note=perform.note,
            )
            steps.append(rec)
            recorder.step(rec)
            if progress:
                progress(
                    "step",
                    persona_id=persona.id,
                    step=step,
                    action=action.type,
                    target=action.element,
                    emotion=action.emotion,
                    thought=action.thought,
                    url=state.url,
                )

            if action.type == "finish":
                outcome, verdict = "completed", action.note or "done"
                break
            if action.type == "give_up":
                outcome, reason = "gave_up", action.note or "fed up"
                break
            if time.monotonic() - wall_start > self.config.budget_seconds:
                outcome, reason = "timeout", "time budget exceeded"
                break
        else:
            outcome, reason = "timeout", f"max steps ({self.config.max_steps}) reached"

        urls = {s.url for s in steps}
        urls.update(s.url_after for s in steps if s.url_after)
        result = SessionResult(
            persona_id=persona.id,
            outcome=outcome,
            steps=len(steps),
            sim_ms=sim_ms,
            wall_ms=int((time.monotonic() - wall_start) * 1000),
            hesitation_ms=hesitation_ms,
            rage_clicks=count_rage_clicks(steps),
            pages_visited=len(urls),
            finish_verdict=verdict,
            gave_up_reason=reason,
        )
        recorder.finalize(result, persona)
        return result, steps

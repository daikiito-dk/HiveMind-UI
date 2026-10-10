"""Data contracts shared across all three HiveMind phases."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Emotion = Literal[
    "neutral", "curious", "confident", "confused", "frustrated", "angry", "satisfied", "bored", "anxious"
]
ActionType = Literal["click", "type_text", "scroll", "back", "wait", "finish", "give_up"]
Outcome = Literal["completed", "gave_up", "timeout", "error"]
Generator = Literal["grok", "mock"]


# ---------------------------------------------------------------- Phase 1
class PersonaTraits(BaseModel):
    """0..1 behavioral knobs. These numbers literally drive the executor."""

    it_literacy: float = Field(ge=0, le=1, description="0 = struggles with QR menus, 1 = power user")
    patience: float = Field(ge=0, le=1, description="0 = 5-second bounce risk, 1 = saint")
    decisiveness: float = Field(ge=0, le=1, description="0 = reads everything twice, 1 = clicks first")
    attention_to_detail: float = Field(ge=0, le=1)
    anxiety: float = Field(ge=0, le=1, description="fear of pressing the wrong thing online")


class XSignal(BaseModel):
    """One grounded insight from live X research (Grok mode)."""

    insight: str
    evidence: list[str] = Field(default_factory=list, description="post gists, handles, or URLs")


class Persona(BaseModel):
    id: str
    archetype: str
    name: str
    age: int
    occupation: str = ""
    location: str = ""
    # Why this person behaves like this *today* (e.g. "this morning's rate hike news").
    context: str = ""
    goal: str = ""
    language: str = "ja"
    personality: PersonaTraits
    self_description: str = ""
    frustrations: list[str] = Field(default_factory=list)
    voice: str = ""
    x_signals: list[XSignal] = Field(default_factory=list)
    give_up_conditions: list[str] = Field(default_factory=list)
    generated_by: Generator = "mock"


class PersonaBatch(BaseModel):
    """Structured-output envelope for one Phase-1 Grok call."""

    x_research_summary: str = ""
    personas: list[Persona] = Field(default_factory=list)


# ---------------------------------------------------------------- Phase 2
class Element(BaseModel):
    index: int
    tag: str
    role: str | None = None
    kind: Literal["link", "button", "input", "other"] = "other"
    label: str = ""
    x: int
    y: int
    w: int
    h: int


class PageState(BaseModel):
    url: str
    title: str = ""
    elements: list[Element] = Field(default_factory=list)
    text_digest: str = ""
    viewport_w: int = 1280
    viewport_h: int = 800


class Action(BaseModel):
    """One persona decision. `thought` is the persona's inner monologue."""

    type: ActionType
    element: int | None = None
    text: str | None = None
    direction: Literal["up", "down"] | None = None
    seconds: float = 0.0
    thought: str = ""
    emotion: Emotion = "neutral"
    hesitate_ms: int = 0
    note: str | None = None


class StepRecord(BaseModel):
    step: int
    t_ms: int
    action: Action
    url: str
    title: str = ""
    shot: str | None = None
    cursor: tuple[int, int] | None = None
    target_label: str | None = None
    url_after: str | None = None
    result_ok: bool = True
    result_note: str | None = None


class SessionResult(BaseModel):
    persona_id: str
    outcome: Outcome
    steps: int
    sim_ms: int
    wall_ms: int
    hesitation_ms: int
    rage_clicks: int
    pages_visited: int
    finish_verdict: str | None = None
    gave_up_reason: str | None = None


# ---------------------------------------------------------------- Phase 3
class Quote(BaseModel):
    moment: str
    quote: str
    emotion: str = "neutral"


class FrictionPoint(BaseModel):
    area: str
    hits: int = 1
    severity: int = Field(default=1, ge=1, le=5)
    why: str
    fix: str


class Report(BaseModel):
    persona_id: str
    headline: str
    task_completed: bool
    satisfaction: float = Field(ge=0, le=1)
    nps_like: int = Field(ge=0, le=10)
    would_return: bool
    verdict: str
    quotes: list[Quote] = Field(default_factory=list)
    friction_points: list[FrictionPoint] = Field(default_factory=list)
    copy_feedback: str = ""
    recommendations: list[str] = Field(default_factory=list)
    generated_by: Generator = "mock"


class CohortReport(BaseModel):
    headline: str
    completion_rate: float
    avg_sim_seconds: float
    avg_hesitation_seconds: float
    total_rage_clicks: int
    drop_reasons: dict[str, int] = Field(default_factory=dict)
    top_friction: list[FrictionPoint] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    generated_by: Generator = "mock"


class RunSummary(BaseModel):
    run_id: str
    mode: str
    model: str
    target_url: str
    task: str
    context: str
    created_at: str
    sessions: list[SessionResult] = Field(default_factory=list)
    cohort: CohortReport | None = None

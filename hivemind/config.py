"""Runtime configuration, loaded from environment (.env) with overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional; only needed when a .env file exists
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    """Everything HiveMind needs to run a cohort."""

    mode: str = "auto"  # auto | mock | grok
    model: str = "grok-4.7"
    api_key: str | None = None
    runs_dir: Path = REPO_ROOT / "runs"
    max_steps: int = 30
    budget_seconds: int = 240
    headless: bool = True
    vision: bool = True
    viewport_w: int = 1280
    viewport_h: int = 800
    # Real-time multiplier for hesitation/typing sleeps during a session.
    # sim_ms recorded in logs always reflects "human" timing, not wall-clock.
    speed: float = 1.0
    # Cohort settings
    persona_count: int = 3
    # X Search window (days back) for persona grounding
    search_days: int = 7
    # Cap X Search rounds to control cost
    search_max_turns: int = 4
    # Screenshots sent to Grok for the Phase 3 analysis (0 = text only)
    analyst_shots: int = 3
    seed: int | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, **overrides) -> Config:
        env = os.environ
        cfg = cls(
            mode=env.get("HIVEMIND_MODE", "auto"),
            model=env.get("HIVEMIND_MODEL", "grok-4.7"),
            api_key=env.get("XAI_API_KEY") or None,
            max_steps=int(env.get("HIVEMIND_MAX_STEPS", "30")),
            budget_seconds=int(env.get("HIVEMIND_BUDGET_SECONDS", "240")),
            headless=env.get("HIVEMIND_HEADLESS", "true").lower() != "false",
            vision=env.get("HIVEMIND_VISION", "true").lower() != "false",
            persona_count=int(env.get("HIVEMIND_PERSONAS", "3")),
            # X Search cost controls: the search window and the number of search
            # rounds per Phase-1 call (search is billed per fetched post).
            search_days=int(env.get("HIVEMIND_SEARCH_DAYS", "7")),
            search_max_turns=int(env.get("HIVEMIND_SEARCH_MAX_TURNS", "4")),
            analyst_shots=int(env.get("HIVEMIND_ANALYST_SHOTS", "3")),
        )
        # Explicitly passed values win, including api_key=None: a caller (or a test)
        # must be able to force mock mode even when a key sits in the environment.
        for key, value in overrides.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        if cfg.seed is not None:
            cfg.extra["seed"] = cfg.seed
        return cfg

    def resolved_mode(self) -> str:
        """Decide the effective mode: grok needs a live API key, else mock."""
        if self.mode == "mock":
            return "mock"
        if self.mode == "grok":
            return "grok"
        return "grok" if self.api_key else "mock"

    def __post_init__(self) -> None:
        if not isinstance(self.runs_dir, Path):
            self.runs_dir = Path(self.runs_dir)

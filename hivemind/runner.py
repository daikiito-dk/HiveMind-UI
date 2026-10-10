"""Cohort orchestration: the full Phase 1 -> 2 -> 3 pipeline for N personas."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .agent import GrokActionPolicy, SessionExecutor
from .analyst import make_analyst, render_cohort_markdown, render_report_markdown
from .behaviors import MockActionPolicy
from .browser import PlaywrightBrowserFactory
from .config import Config
from .grok import GrokClient, GrokError
from .persona import make_persona_generator
from .recorder import SessionRecorder
from .schemas import Persona, Report, RunSummary, SessionResult, StepRecord

STRONG_EMOTIONS = ("angry", "frustrated", "confused")


def _key_shots(recorder: SessionRecorder, steps: list[StepRecord], k: int) -> list[bytes]:
    """Pick up to k screenshots from the most emotionally loaded steps."""
    ranked = sorted(
        [s for s in steps if s.shot],
        key=lambda s: 0 if s.action.emotion == "angry" else 1 if s.action.emotion in ("frustrated", "confused") else 2,
    )
    shots: list[bytes] = []
    for s in ranked[:k]:
        path = recorder.shots_dir / Path(s.shot).name
        if path.exists():
            shots.append(path.read_bytes())
    return shots


def run_cohort(
    config: Config,
    target_url: str,
    task: str,
    context: str = "",
    count: int | None = None,
    language: str = "ja",
    progress=None,
    browser_factory=None,
    make_gif: bool = True,
) -> tuple[RunSummary, Path]:
    """Generate personas, run each through the target, analyze, and write artifacts."""

    def emit(event: str, **data) -> None:
        if progress:
            progress(event, **data)

    mode = config.resolved_mode()
    count = count or config.persona_count
    now = datetime.now()
    run_id = now.strftime("%Y%m%d-%H%M%S") + f"-{mode}"
    run_dir = config.runs_dir / run_id
    for sub in ("personas", "sessions", "reports"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    grok = GrokClient(config) if mode == "grok" else None
    factory = browser_factory or PlaywrightBrowserFactory(config)

    # ---- Phase 1: personas -------------------------------------------------
    emit("phase", phase=1, message="ペルソナ生成" + ("（Grok + X Search）" if mode == "grok" else "（オフライン・モック）"))
    generator = make_persona_generator(config, grok)
    batch = generator.generate(context=context, task=task, count=count, language=language, seed=config.seed)
    personas = batch.personas
    if not personas:
        # A cohort of zero personas is not a result. Writing reports from it would look
        # like a finding ("nothing could be tested") when it is a failure to generate.
        raise GrokError("Phase 1 produced no personas; refusing to run an empty cohort.")
    for i, p in enumerate(personas, 1):
        if not p.id:
            p.id = f"p{i}"
    (run_dir / "personas" / "batch.json").write_text(
        json.dumps(json.loads(batch.model_dump_json()), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    emit("personas", personas=personas, research=batch.x_research_summary)

    # ---- Phase 2 + 3, one persona at a time --------------------------------
    results: list[SessionResult] = []
    reports: list[Report] = []
    analyst = make_analyst(config, grok)
    seed0 = config.seed if config.seed is not None else 42

    executor = SessionExecutor(config, target_url)
    for i, persona in enumerate(personas):
        emit("session_start", persona=persona, index=i + 1, total=len(personas))
        recorder = SessionRecorder(run_dir, persona)
        (run_dir / "personas" / f"{persona.id}.json").write_text(
            persona.model_dump_json(indent=2), encoding="utf-8"
        )
        if mode == "grok":
            policy = GrokActionPolicy(grok, persona, task)
        else:
            policy = MockActionPolicy(persona, task, seed=seed0 * 1009 + i)

        browser = factory.open()
        try:
            result, steps = executor.run(persona, policy, browser, recorder, progress=emit)
        finally:
            browser.close()

        recorder.build_replays(result, gif=make_gif)

        shots = _key_shots(recorder, steps, config.analyst_shots) if mode == "grok" else []
        emit("phase", phase=3, persona_id=persona.id, message="レビュー解析")
        report = analyst.review(persona, result, steps, shots=shots)
        report.persona_id = persona.id

        (run_dir / "reports" / f"{persona.id}.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
        (run_dir / "reports" / f"{persona.id}.md").write_text(
            render_report_markdown(report, persona, result), encoding="utf-8"
        )
        results.append(result)
        reports.append(report)
        emit("session_done", persona=persona, result=result, report=report)

    # ---- Cohort synthesis ----------------------------------------------------
    emit("phase", phase=3, message="コホート集計")
    cohort = analyst.summarize_cohort(reports, results, personas, task=task)
    (run_dir / "cohort.json").write_text(cohort.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "cohort_report.md").write_text(
        render_cohort_markdown(cohort, run_id, task, results), encoding="utf-8"
    )

    summary = RunSummary(
        run_id=run_id,
        mode=mode,
        model=cfg_model(config),
        target_url=target_url,
        task=task,
        context=context,
        created_at=now.isoformat(timespec="seconds"),
        sessions=results,
        cohort=cohort,
    )
    (run_dir / "run.json").write_text(
        json.dumps(json.loads(summary.model_dump_json()), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if grok is not None:
        (run_dir / "usage.json").write_text(
            json.dumps(grok.usage, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    factory.close()
    emit("done", run_dir=str(run_dir), summary=summary)
    return summary, run_dir


def cfg_model(config: Config) -> str:
    return config.model if config.resolved_mode() == "grok" else "mock-behavior-model"


def list_runs(runs_dir: Path, limit: int = 50) -> list[RunSummary]:
    out: list[RunSummary] = []
    if not runs_dir.exists():
        return out
    for run_json in sorted(runs_dir.glob("*/run.json"), reverse=True)[:limit]:
        try:
            out.append(RunSummary.model_validate_json(run_json.read_text(encoding="utf-8")))
        except Exception:
            continue
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


def load_steps(run_dir: Path, persona_id: str) -> list[StepRecord]:
    path = run_dir / "sessions" / persona_id / "log.jsonl"
    if not path.exists():
        return []
    steps = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            steps.append(StepRecord.model_validate_json(line))
    return steps


def load_persona(run_dir: Path, persona_id: str) -> Persona | None:
    path = run_dir / "personas" / f"{persona_id}.json"
    if not path.exists():
        return None
    return Persona.model_validate_json(path.read_text(encoding="utf-8"))


def load_report(run_dir: Path, persona_id: str) -> Report | None:
    path = run_dir / "reports" / f"{persona_id}.json"
    if not path.exists():
        return None
    return Report.model_validate_json(path.read_text(encoding="utf-8"))

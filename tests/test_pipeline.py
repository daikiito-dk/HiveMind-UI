"""End-to-end pipeline test, fully offline (fake browser + mock brain)."""

import json

from hivemind.config import Config
from hivemind.fake_browser import FakeBrowserFactory
from hivemind.runner import run_cohort

TASK = "他行あてに3万円を振り込む"


def run(tmp_path, seed: int = 7, count: int = 3):
    config = Config.from_env(
        mode="mock",
        runs_dir=tmp_path / "runs",
        seed=seed,
        speed=10_000.0,  # no real sleeping
        max_steps=25,
        budget_seconds=60,
        persona_count=count,
        headless=True,
    )
    events: list[tuple[str, dict]] = []

    def progress(event: str, **data):
        events.append((event, {k: v for k, v in data.items() if k not in ("persona", "report")}))

    summary, run_dir = run_cohort(
        config,
        target_url="https://example.com",
        task=TASK,
        context="節約志向が高まっている",
        count=count,
        progress=progress,
        browser_factory=FakeBrowserFactory(),
        make_gif=True,
    )
    return summary, run_dir, events


def test_pipeline_artifacts(tmp_path):
    summary, run_dir, events = run(tmp_path, seed=7, count=3)

    assert (run_dir / "run.json").exists()
    assert (run_dir / "cohort_report.md").exists()
    assert (run_dir / "cohort.json").exists()
    assert len(list((run_dir / "personas").glob("p*.json"))) == 3
    assert len(list((run_dir / "reports").glob("p*.md"))) == 3
    assert len(summary.sessions) == 3
    assert summary.cohort is not None
    assert summary.mode == "mock"

    for session_dir in (run_dir / "sessions").iterdir():
        assert (session_dir / "log.jsonl").exists()
        assert (session_dir / "session.json").exists()
        assert (session_dir / "replay.html").exists()
        lines = (session_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 3
        for line in lines:
            rec = json.loads(line)
            assert rec["action"]["type"] in ("click", "type_text", "scroll", "back", "wait", "finish", "give_up")
            assert rec["action"]["thought"]

    md = (run_dir / "cohort_report.md").read_text(encoding="utf-8")
    assert "コホートレポート" in md
    assert summary.cohort.headline in md

    kinds = [e[0] for e in events]
    assert "phase" in kinds and "personas" in kinds and "done" in kinds
    assert kinds.count("session_start") == 3


def test_pipeline_is_deterministic(tmp_path):
    summary_a, run_a, _ = run(tmp_path, seed=7)
    summary_b, run_b, _ = run(tmp_path, seed=7)
    outcomes_a = [(s.persona_id, s.outcome, s.steps, s.rage_clicks) for s in summary_a.sessions]
    outcomes_b = [(s.persona_id, s.outcome, s.steps, s.rage_clicks) for s in summary_b.sessions]
    assert outcomes_a == outcomes_b
    # and reports agree
    for pid, *_ in outcomes_a:
        ra = (run_a / "reports" / f"{pid}.json").read_text(encoding="utf-8")
        rb = (run_b / "reports" / f"{pid}.json").read_text(encoding="utf-8")
        assert json.loads(ra)["satisfaction"] == json.loads(rb)["satisfaction"]


def test_pipeline_progressive_outcomes(tmp_path):
    """With a fixed seed we get a mix: at least one completion or a tracked drop."""
    summary, _, _ = run(tmp_path, seed=7)
    outcomes = {s.outcome for s in summary.sessions}
    assert outcomes & {"completed", "gave_up", "timeout"}

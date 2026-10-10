#!/usr/bin/env python
"""HiveMind-UI command line interface.

Examples:
  uv run python cli.py run --url "file://samples/retro_bank.html" \
      --task "残高を確認して、他行あての振込画面までたどり着く" --personas 3
  uv run python cli.py runs                 # list past runs
  uv run python cli.py report runs/<id>     # print the cohort report
  uv run python cli.py replay runs/<id> p1  # path to the animated replay
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hivemind.config import Config
from hivemind.grok import GrokError
from hivemind.recorder import OUTCOME_JA
from hivemind.runner import list_runs, load_report, run_cohort

REPO_ROOT = Path(__file__).resolve().parent
IS_TTY = sys.stdout.isatty()


def dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if IS_TTY else text


def bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if IS_TTY else text


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if IS_TTY else text


def _progress(event: str, **data) -> None:
    if event == "phase":
        print(bold(f"\n-- {data.get('message', '')} --"))
    elif event == "personas":
        for p in data.get("personas", []):
            t = p.personality
            print(
                f"  persona {p.id}: {p.name} ({p.age}, {p.archetype}) "
                + dim(f"literacy {t.it_literacy:.2f} patience {t.patience:.2f}")
            )
    elif event == "session_start":
        p = data["persona"]
        print(bold(f"\n>> [{data['index']}/{data['total']}] {p.name} ({p.archetype}) を憑依開始"))
    elif event == "step":
        mark = {"click": "", "type_text": "typing", "scroll": "scroll", "back": "back", "wait": "wait", "finish": "FINISH", "give_up": "GIVE UP"}.get(data["action"], "")
        emo = color(data["emotion"], "31" if data["emotion"] in ("angry", "frustrated") else "33")
        print(f"   step {data['step']:>2} {mark:<8} [{emo}] {data['thought'][:64]}")
    elif event == "session_done":
        r = data["result"]
        outcome = color(OUTCOME_JA.get(r.outcome, r.outcome), "32" if r.outcome == "completed" else "31")
        print(f"   => {outcome} / steps {r.steps} / rage {r.rage_clicks}")


def cmd_run(args: argparse.Namespace) -> int:
    url = args.url
    if not url.startswith(("http://", "https://", "file://")):
        path = Path(url)
        if not path.is_absolute():
            path = REPO_ROOT / path
        url = path.resolve().as_uri()

    config = Config.from_env(
        mode=args.mode,
        max_steps=args.max_steps,
        budget_seconds=args.budget,
        speed=args.speed,
        headless=not args.show,
        persona_count=args.personas,
        seed=args.seed,
    )
    print(bold(f"HiveMind-UI | mode={config.resolved_mode()} model={config.model} target={url}"))
    try:
        summary, run_dir = run_cohort(
            config,
            target_url=url,
            task=args.task,
            context=args.context,
            count=args.personas,
            progress=_progress,
            make_gif=not args.no_gif,
        )
    except GrokError as exc:
        # Fail loudly and with a non-zero status: a cohort that never ran must not look
        # like a cohort that ran and found nothing.
        print(color(f"\n失敗: {exc}", "31"))
        return 1
    print(bold(f"\nDone. Artifacts in {run_dir}"))
    print(f"  cohort report : {run_dir / 'cohort_report.md'}")
    print(f"  per-persona   : {run_dir / 'reports'}")
    print(f"  replays       : {run_dir / 'sessions'}")
    if summary.cohort:
        print(bold(f"\n{summary.cohort.headline}"))
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    runs = list_runs(Config.from_env().runs_dir)
    if not runs:
        print("No runs yet. Try: uv run python cli.py run --personas 3")
        return 1
    for r in runs:
        done = sum(1 for s in r.sessions if s.outcome == "completed")
        rate = f"{done}/{len(r.sessions)}"
        print(f"{r.run_id:<24} {r.mode:<5} done={rate:<7} {r.task[:38]}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        run_dir = Config.from_env().runs_dir / run_dir
    md = run_dir / "cohort_report.md"
    if md.exists():
        print(md.read_text(encoding="utf-8"))
        return 0
    print(f"No cohort report at {md}")
    return 1


def cmd_replay(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        run_dir = Config.from_env().runs_dir / run_dir
    candidates = sorted((run_dir / "sessions" / args.persona).glob("replay.*")) if args.persona else []
    if not candidates:
        for session in sorted((run_dir / "sessions").glob("*")):
            if (session / "replay.html").exists():
                print(session / "replay.html")
        return 0
    for path in candidates:
        print(path)
    return 0


def cmd_persona(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        run_dir = Config.from_env().runs_dir / run_dir
    report = load_report(run_dir, args.persona)
    if report is None:
        print(f"No report for {args.persona} in {run_dir}")
        return 1
    md = run_dir / "reports" / f"{args.persona}.md"
    if md.exists():
        print(md.read_text(encoding="utf-8"))
    else:
        print(report.model_dump_json(indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hivemind", description="HiveMind-UI: persona-linked autonomous UI testing")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a persona cohort against a target URL")
    run.add_argument("--url", required=True, help="Target URL (http(s):// or a path/ file path)")
    run.add_argument("--task", required=True, help="What the personas try to accomplish")
    run.add_argument("--context", default="", help="Live context that shapes the personas (JA text works best)")
    run.add_argument("--personas", type=int, default=3)
    run.add_argument("--mode", choices=["auto", "mock", "grok"], default="auto")
    run.add_argument("--speed", type=float, default=2.0, help="Replay speed multiplier for sleeps (default 2)")
    run.add_argument("--max-steps", type=int, default=30)
    run.add_argument("--budget", type=int, default=240, help="Wall-clock budget per persona, seconds")
    run.add_argument("--seed", type=int, default=None, help="Deterministic personas/behavior (mock mode)")
    run.add_argument("--show", action="store_true", help="Show the Chromium window (not headless)")
    run.add_argument("--no-gif", action="store_true", help="Skip GIF generation (faster)")
    run.set_defaults(func=cmd_run)

    runs = sub.add_parser("runs", help="List past runs")
    runs.set_defaults(func=cmd_runs)

    report = sub.add_parser("report", help="Print a cohort report")
    report.add_argument("run", help="Run id or path")
    report.set_defaults(func=cmd_report)

    persona = sub.add_parser("persona", help="Print one persona's report")
    persona.add_argument("run")
    persona.add_argument("persona", help="Persona id, e.g. p1")
    persona.set_defaults(func=cmd_persona)

    replay = sub.add_parser("replay", help="List replay artifact paths")
    replay.add_argument("run")
    replay.add_argument("persona", nargs="?", default=None)
    replay.set_defaults(func=cmd_replay)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

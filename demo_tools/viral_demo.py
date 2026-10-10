#!/usr/bin/env python
"""Build in Public asset generator.

Runs a scripted offline mock cohort against the deliberately-terrible retro bank
sample and produces postable assets:
  runs/<run_id>/sessions/*/replay.gif   - cursor-trace GIFs (X-friendly)
  runs/<run_id>/x_post.md               - suggested X post copy (JA + EN) with metrics

Usage: uv run python demo_tools/viral_demo.py
"""

from __future__ import annotations

from pathlib import Path

from hivemind.config import Config
from hivemind.recorder import OUTCOME_JA
from hivemind.runner import load_steps, run_cohort

REPO = Path(__file__).resolve().parent.parent
SEED = 20261010

CONTEXT = (
    "物価高のニュースが今朝流れた直後。SNSでは「何も買うな」がトレンド入りし、"
    "節約志向が爆発中。銀行の手数料にも神経質になっている。"
)
TASK = "他行あてに3万円を振り込む"


def main() -> None:
    url = (REPO / "samples" / "retro_bank.html").as_uri()
    config = Config.from_env(
        mode="mock",
        seed=SEED,
        speed=6.0,
        persona_count=4,
        max_steps=26,
        headless=True,
    )
    print("=" * 62)
    print("HiveMind-UI viral demo run (offline mock, deterministic)")
    print("=" * 62)

    summary, run_dir = run_cohort(config, target_url=url, task=TASK, context=CONTEXT, count=4)

    drama = sorted(
        summary.sessions,
        key=lambda s: (s.rage_clicks, s.outcome != "completed", s.sim_ms),
        reverse=True,
    )[0]
    persona_files = sorted((run_dir / "personas").glob("*.json"))
    import json

    persona = None
    for f in persona_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("id") == drama.persona_id:
            persona = data
            break

    steps = load_steps(run_dir, drama.persona_id)
    hot = [s for s in steps if s.action.emotion == "angry"] or [s for s in steps if s.action.thought]
    quote = hot[0].action.thought if hot else "（無言の離脱）"

    gif = run_dir / "sessions" / drama.persona_id / "replay.gif"
    name = persona["name"] if persona else drama.persona_id
    age = persona.get("age", "?") if persona else "?"
    archetype = persona.get("archetype", "") if persona else ""
    sec = round(drama.sim_ms / 1000)
    hes = round(drama.hesitation_ms / 1000, 1)
    outcome = OUTCOME_JA.get(drama.outcome, drama.outcome)

    ja = f"""🐝【Grokがペルソナを生成し、その人格になりきって1998年風の銀行サイトを操作した結果】

本日の被験者: {name}（{age}・{archetype}）
結果: {outcome}｜滞在 {sec}秒｜迷い {hes}秒｜怒りクリック {drama.rage_clicks}回

「{quote}」

— Xの今の空気からペルソナを生成（Phase 1）
— ペルソナのITリテラシー・忍耐力どおりに、実際のUIを操作（Phase 2）
— 操作ログを主観インタビューに翻訳（Phase 3）

人間のテスター100人のリードタイム＝数週間。
コンテキストの異なるペルソナ100体＝数分。

操作の様子（GIF）はこのスレに続きます。
#BuildInPublic #Grok #xAI #ComputerUse #UX"""

    done = sum(1 for s in summary.sessions if s.outcome == "completed")
    en = f"""[Built a UI testing agent that BECOMES the user]

HiveMind-UI generates personas from what X is talking about RIGHT NOW (Phase 1),
drives a real browser as that persona - including the confusion of low IT literacy
(Phase 2) - then writes the qualitative debrief (Phase 3).

Today's victim: {name} ({age}, {archetype}) vs a 1998-style bank site.
Result: {drama.outcome} after {sec}s, {drama.rage_clicks} rage clicks.
"{quote}"

Cohort: {done}/{len(summary.sessions)} personas completed the task.
Replacing weeks of human tester recruiting with minutes.

GIFs in this thread.
#BuildInPublic #Grok #xAI #ComputerUse"""

    post = ja + "\n\n---\n\n" + en + f"\n\nAssets:\n- GIF: {gif}\n- Replay: {run_dir / 'sessions' / drama.persona_id / 'replay.html'}\n- Cohort report: {run_dir / 'cohort_report.md'}\n"
    (run_dir / "x_post.md").write_text(post, encoding="utf-8")

    print()
    print("=" * 62)
    print("Suggested X post (saved to x_post.md):")
    print("=" * 62)
    print(ja)
    print()
    print("GIF:", gif)
    print("Run dir:", run_dir)


if __name__ == "__main__":
    main()

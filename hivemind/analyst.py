"""Phase 3 - subjective review (per persona) and cohort synthesis.

MockAnalyst: deterministic, rule-based, honest about being synthetic.
GrokAnalyst: re-reads the behavior log (and key screenshots) through Grok and
returns an interview-style debrief in the persona's own voice.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from .config import Config
from .grok import GrokClient
from .prompts import ANALYST_SYSTEM_PROMPT, COHORT_SYSTEM_PROMPT, analyst_user_payload
from .schemas import (
    CohortReport,
    FrictionPoint,
    Persona,
    Quote,
    Report,
    SessionResult,
    StepRecord,
)

STRUGGLE_EMOTIONS = ("confused", "frustrated", "angry", "anxious")
EMOTION_PRIORITY = {"angry": 0, "frustrated": 1, "confused": 2, "anxious": 3, "satisfied": 4, "curious": 5}

FIX_RULES: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"訂正|戻る|取消|クリア"),
        "主操作と副操作の区別がない",
        "主操作（実行）と副操作（訂正・戻る）を色・サイズ・位置で明確に分離する",
    ),
    (
        re.compile(r"通常払|被仕向|仕向|払込み"),
        "金融制度用語がそのまま並んでいる",
        "制度用語をやめ、「振込先を選ぶ」「送る」など一般向けの言葉に置き換える",
    ),
    (
        re.compile(r"番号|暗証|パスワード|pass|pin", re.IGNORECASE),
        "入力欄の用途と結果が不安になる",
        "ラベルを平易な言葉にし、入力例と「押しても安全であること」を明記する",
    ),
    (
        re.compile(r"FAQ|お知らせ|一覧|閉じる|ポップ|リンク集"),
        "脱線させるリンクや割り込みが操作を邪魔している",
        "割り込み表示を廃止し、脱線リンクを主要操作から視覚的に切り離す",
    ),
    (
        re.compile(r"確認"),
        "確認操作の連鎖で先が見えない",
        "確認ステップを1回にまとめ、次に押すべきボタンを画面の主位置に置く",
    ),
    (
        re.compile(r"口座開設|ローン|外貨|投資|残高照会"),
        "主要操作と同列のリンクが並び、目的の操作が埋もれている",
        "主要操作をファーストビューの大きなカードにし、その他は折りたたむ",
    ),
    (
        re.compile(r"^back @|^wait @|click @"),
        "迷子になったときの戻り方が分からない",
        "パンくずと明確な戻るボタンを置き、位置を変えない",
    ),
]
DEFAULT_FRICTION = (
    "主要な操作の位置や名前が予想とずれる",
    "目的の操作を「動詞+目的語」のボタン名（例: 振込する）にして、画面上部に置く",
)

LABEL_RE = re.compile(r"「(.+?)」")


def _fmt(ms: int) -> str:
    m, s = divmod(ms // 1000, 60)
    return f"{m}分{s:02d}秒" if m else f"{s}秒"


def _area_of(step: StepRecord) -> str:
    if step.target_label:
        return step.target_label
    if step.result_note and LABEL_RE.search(step.result_note):
        return LABEL_RE.search(step.result_note).group(1)
    if step.action.emotion in STRUGGLE_EMOTIONS:
        return f"{step.action.type} @ {step.title}"
    return step.action.type


class MockAnalyst:
    def review(
        self,
        persona: Persona,
        result: SessionResult,
        steps: list[StepRecord],
        shots: list[bytes] | None = None,
    ) -> Report:
        by_area: dict[str, FrictionPoint] = {}
        for s in steps:
            if s.action.emotion not in STRUGGLE_EMOTIONS:
                continue
            area = _area_of(s)
            fp = by_area.get(area)
            if fp is None:
                why = s.action.thought[:120] or "停滞が観測された"
                fix = DEFAULT_FRICTION[1]
                for pattern, why_rule, fix_rule in FIX_RULES:
                    if pattern.search(area):
                        why, fix = why_rule, fix_rule
                        break
                by_area[area] = FrictionPoint(area=area, hits=1, severity=1, why=why, fix=fix)
            else:
                fp.hits += 1
        for fp in by_area.values():
            fp.severity = min(5, 1 + fp.hits // 2 + (1 if result.outcome != "completed" else 0))
        frictions = sorted(by_area.values(), key=lambda f: (f.severity, f.hits), reverse=True)

        hes_norm = min(1.0, result.hesitation_ms / 25_000)
        satisfaction = 0.78 if result.outcome == "completed" else 0.24
        satisfaction -= 0.25 * hes_norm + 0.04 * result.rage_clicks
        satisfaction += 0.05 * persona.personality.decisiveness
        satisfaction = max(0.0, min(1.0, satisfaction))

        ranked = sorted(
            [s for s in steps if s.action.thought],
            key=lambda s: EMOTION_PRIORITY.get(s.action.emotion, 9),
        )
        quotes = [
            Quote(moment=f"step {s.step}: {s.action.type}" + (f" ({_area_of(s)})" if s.action.emotion in STRUGGLE_EMOTIONS else ""), quote=s.action.thought, emotion=s.action.emotion)
            for s in ranked[:5]
        ]

        sec = _fmt(result.sim_ms)
        if result.outcome == "completed":
            verdict = (
                f"{persona.name}（{persona.age}歳・{persona.archetype}）は{result.steps}ステップ・{sec}でタスクを完了した。"
                f"迷いは{_fmt(result.hesitation_ms)}、怒りクリック{result.rage_clicks}回。"
            )
        elif result.outcome == "gave_up":
            verdict = f"{persona.name}は{sec}で離脱した。離脱理由: {result.gave_up_reason or '不明'}。"
        elif result.outcome == "timeout":
            verdict = f"{persona.name}は制限時間内にタスクを完了できなかった（{sec}で打ち切り）。"
        else:
            verdict = f"{persona.name}のセッションはエラーで中断した: {result.gave_up_reason or ''}"

        headline = (
            f"最大の摩擦は「{frictions[0].area}」（{frictions[0].hits}回の停滞）"
            if frictions
            else "摩擦らしい摩擦は観測されず、タスクはすんなり進んだ"
        )

        copy_hits = [s for s in steps if re.search(r"文字|小さ|用語|言葉|読め", s.action.thought)]
        copy_feedback = "文字の小ささ・用語への不満が行動ログから観測された。" if copy_hits else ""

        return Report(
            persona_id=persona.id,
            headline=headline,
            task_completed=result.outcome == "completed",
            satisfaction=round(satisfaction, 2),
            nps_like=int(round(satisfaction * 10)),
            would_return=satisfaction >= 0.55,
            verdict=verdict,
            quotes=quotes,
            friction_points=frictions,
            copy_feedback=copy_feedback,
            recommendations=list(dict.fromkeys(f.fix for f in frictions))[:3],
            generated_by="mock",
        )

    def summarize_cohort(
        self,
        reports: list[Report],
        results: list[SessionResult],
        personas: list[Persona],
        task: str = "",
    ) -> CohortReport:
        total = max(1, len(results))
        completed = sum(1 for r in results if r.outcome == "completed")
        avg_sim = sum(r.sim_ms for r in results) / total
        avg_hes = sum(r.hesitation_ms for r in results) / total

        merged: dict[str, FrictionPoint] = {}
        for rep in reports:
            for fp in rep.friction_points:
                m = merged.get(fp.area)
                if m is None:
                    merged[fp.area] = FrictionPoint(area=fp.area, hits=fp.hits, severity=fp.severity, why=fp.why, fix=fp.fix)
                else:
                    m.hits += fp.hits
                    m.severity = max(m.severity, fp.severity)
        top = sorted(merged.values(), key=lambda f: (f.severity, f.hits), reverse=True)[:5]

        quotes: list[Quote] = []
        for bucket in zip(*(rep.quotes for rep in reports), strict=False):
            quotes.extend(bucket)
            if len(quotes) >= 6:
                break
        quotes = quotes[:6]

        drop_reasons: Counter[str] = Counter()
        for r in results:
            if r.outcome == "completed":
                continue
            key = (r.gave_up_reason or r.outcome)[:40]
            drop_reasons[key] += 1

        recs: list[str] = []
        for rep in reports:
            for rec in rep.recommendations:
                if rec not in recs:
                    recs.append(rec)
        recs = recs[:5]

        headline = f"{len(results)}ペルソナ中{completed}人がタスク完了。"
        if top:
            headline += f"最大の摩擦は「{top[0].area}」（{top[0].hits}回の停滞）。"
        else:
            headline += "顕著な摩擦は観測されなかった。"

        return CohortReport(
            headline=headline,
            completion_rate=round(completed / total, 2),
            avg_sim_seconds=round(avg_sim / 1000, 1),
            avg_hesitation_seconds=round(avg_hes / 1000, 1),
            total_rage_clicks=sum(r.rage_clicks for r in results),
            drop_reasons=dict(drop_reasons),
            top_friction=top,
            quotes=quotes,
            recommendations=recs,
            generated_by="mock",
        )


class GrokAnalyst:
    def __init__(self, client: GrokClient) -> None:
        self.client = client

    def review(
        self,
        persona: Persona,
        result: SessionResult,
        steps: list[StepRecord],
        shots: list[bytes] | None = None,
    ) -> Report:
        payload = analyst_user_payload(persona, result, steps)
        report = self.client.review_session(ANALYST_SYSTEM_PROMPT, payload, shots or [])
        report.persona_id = persona.id
        report.task_completed = result.outcome == "completed"  # trust the log, not the model's wish
        report.generated_by = "grok"
        return report

    def summarize_cohort(
        self,
        reports: list[Report],
        results: list[SessionResult],
        personas: list[Persona],
        task: str = "",
    ) -> CohortReport:
        computed = MockAnalyst().summarize_cohort(reports, results, personas, task)
        payload = {
            "task": task,
            "metrics": {
                "completion_rate": computed.completion_rate,
                "avg_sim_seconds": computed.avg_sim_seconds,
                "avg_hesitation_seconds": computed.avg_hesitation_seconds,
                "total_rage_clicks": computed.total_rage_clicks,
            },
            "per_persona": [
                {
                    "persona_id": r.persona_id,
                    "headline": r.headline,
                    "verdict": r.verdict,
                    "satisfaction": r.satisfaction,
                    "friction": [{"area": f.area, "hits": f.hits, "severity": f.severity, "why": f.why} for f in r.friction_points],
                    "quotes": [q.model_dump() for q in r.quotes],
                }
                for r in reports
            ],
        }
        cohort = self.client.review_cohort(
            COHORT_SYSTEM_PROMPT,
            json.dumps(payload, ensure_ascii=False),
        )
        # Numbers come from the data, text comes from Grok.
        cohort.completion_rate = computed.completion_rate
        cohort.avg_sim_seconds = computed.avg_sim_seconds
        cohort.avg_hesitation_seconds = computed.avg_hesitation_seconds
        cohort.total_rage_clicks = computed.total_rage_clicks
        cohort.drop_reasons = computed.drop_reasons
        cohort.generated_by = "grok"
        return cohort


def make_analyst(config: Config, grok: GrokClient | None):
    if config.resolved_mode() == "grok":
        if grok is None:
            raise ValueError("grok mode requires a GrokClient")
        return GrokAnalyst(grok)
    return MockAnalyst()


# ------------------------------------------------------------- markdown out
def render_report_markdown(report: Report, persona: Persona, result: SessionResult) -> str:
    lines = [
        f"## {persona.name}（{persona.age}・{persona.archetype}） - {report.headline}",
        "",
        f"> {report.verdict}",
        "",
        "| 項目 | 値 |",
        "| --- | --- |",
        f"| 結果 | {'完了' if report.task_completed else '未完了'} |",
        f"| ステップ数 / 滞在時間 | {result.steps} / {_fmt(result.sim_ms)} |",
        f"| 迷い時間（合計） | {_fmt(result.hesitation_ms)} |",
        f"| 怒りクリック | {result.rage_clicks} |",
        f"| 満足度 / NPS的スコア | {report.satisfaction:.2f} / {report.nps_like} |",
        f"| また使いたいか | {'はい' if report.would_return else 'いいえ'} |",
        f"| 生成 | {report.generated_by} |",
        "",
    ]
    if report.quotes:
        lines.append("### ペルソナの声（行動ログからの引用）")
        for q in report.quotes:
            lines.append(f"- {q.moment}: 「{q.quote}」 *({q.emotion})*")
        lines.append("")
    if report.friction_points:
        lines.append("### 摩擦ポイント")
        lines.append("| 画面・要素 | 停滞 | 深刻度 | なぜ | 改善 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for f in report.friction_points:
            lines.append(f"| {f.area} | {f.hits} | {f.severity}/5 | {f.why} | {f.fix} |")
        lines.append("")
    if report.recommendations:
        lines.append("### 改善の優先順位")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"{i}. {rec}")
        lines.append("")
    if report.copy_feedback:
        lines.append("### コピーへの反応")
        lines.append(report.copy_feedback)
        lines.append("")
    return "\n".join(lines)


def render_cohort_markdown(cohort: CohortReport, run_id: str, task: str, results: list[SessionResult]) -> str:
    lines = [
        f"# コホートレポート - {run_id}",
        "",
        f"**タスク**: {task}",
        "",
        f"> {cohort.headline}",
        "",
        "| 指標 | 値 |",
        "| --- | --- |",
        f"| 完了率 | {cohort.completion_rate * 100:.0f}% ({sum(1 for r in results if r.outcome == 'completed')}/{len(results)}) |",
        f"| 平均滞在時間 | {cohort.avg_sim_seconds:.0f}秒 |",
        f"| 平均迷い時間 | {cohort.avg_hesitation_seconds:.1f}秒 |",
        f"| 怒りクリック合計 | {cohort.total_rage_clicks} |",
        f"| 生成 | {cohort.generated_by} |",
        "",
    ]
    if cohort.drop_reasons:
        lines.append("### 離脱理由")
        for reason, n in cohort.drop_reasons.items():
            lines.append(f"- {reason} × {n}")
        lines.append("")
    if cohort.top_friction:
        lines.append("### 摩擦ポイント（コホート横断）")
        lines.append("| 画面・要素 | 停滞 | 深刻度 | なぜ | 改善 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for f in cohort.top_friction:
            lines.append(f"| {f.area} | {f.hits} | {f.severity}/5 | {f.why} | {f.fix} |")
        lines.append("")
    if cohort.quotes:
        lines.append("### 代表ボイス")
        for q in cohort.quotes:
            lines.append(f"- 「{q.quote}」 *({q.emotion})*")
        lines.append("")
    if cohort.recommendations:
        lines.append("### 推奨アクション（Impact順）")
        for i, rec in enumerate(cohort.recommendations, 1):
            lines.append(f"{i}. {rec}")
        lines.append("")
    return "\n".join(lines)

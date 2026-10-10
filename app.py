"""HiveMind-UI dashboard (Streamlit).

Run with: make app  (or `uv run streamlit run app.py`)

Left: configure a cohort and launch it. Main: watch personas live, open their
animated replays, and read the Phase-3 qualitative reports.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from hivemind.config import Config
from hivemind.recorder import OUTCOME_JA
from hivemind.runner import list_runs, load_persona, load_report, load_steps, run_cohort

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_TARGET = (REPO_ROOT / "samples" / "retro_bank.html").resolve().as_uri()
DEFAULT_TASK = "残高を確認して、他行あての振込を完了させる"

st.set_page_config(page_title="HiveMind-UI", page_icon="🐝", layout="wide")

st.title("🐝 HiveMind-UI")
st.caption(
    "リアルタイム・ペルソナ連動型 自律UIテストエージェント — Grok × Computer Use. "
    "Phase 1: ペルソナ生成 → Phase 2: 自律ブラウザ操作 → Phase 3: 定性レビュー"
)

config = Config.from_env()


def sidebar_run_form() -> dict | None:
    with st.sidebar:
        st.header("コホート実行")
        url = st.text_input("対象URL", value=DEFAULT_TARGET)
        task = st.text_input("タスク", value=DEFAULT_TASK)
        context = st.text_area(
            "今のコンテキスト（GrokはXを検索して反映 / モックは擬似的に反映）",
            value="今朝のニュースで物価高が報じられた直後。節約志向が高まっている。",
        )
        col1, col2 = st.columns(2)
        with col1:
            count = st.number_input("ペルソナ数", 1, 8, 3)
            speed = st.slider("倍速", 1.0, 8.0, 3.0, 0.5)
        with col2:
            mode_label = st.selectbox("モード", ["auto", "mock", "grok"], index=0)
            headless = st.checkbox("headless", value=True)
        max_steps = st.slider("最大ステップ数", 8, 40, 30)
        launched = st.button("▶ コホートを実行", type="primary", use_container_width=True)
        st.caption(
            f"mode=auto → **{config.resolved_mode()}** "
            + ("(XAI_API_KEY あり: Grok + X Search)" if config.resolved_mode() == "grok" else "(キーなし: オフライン・モック)")
        )
        if not launched:
            return None
        return dict(url=url, task=task, context=context, count=int(count), speed=float(speed),
                    mode_label=mode_label, headless=headless, max_steps=int(max_steps))


def show_live_progress(form: dict) -> tuple:
    run_cfg = Config.from_env(
        mode=form["mode_label"],
        headless=form["headless"],
        speed=form["speed"],
        persona_count=form["count"],
        max_steps=form["max_steps"],
    )
    status = st.status("Phase 1: ペルソナ生成…", expanded=True)
    step_lines = st.empty()
    log: list[str] = []

    def progress(event: str, **data) -> None:
        if event == "phase":
            status.update(label=f"Phase {data.get('phase', '')}: {data.get('message', '')}", state="running")
        elif event == "personas":
            names = " / ".join(f"{p.name}({p.archetype})" for p in data["personas"])
            log.append(f"ペルソナ: {names}")
        elif event == "session_start":
            p = data["persona"]
            log.append(f"--- {data['index']}/{data['total']} {p.name} ({p.archetype}) 憑依開始 ---")
        elif event == "step":
            log.append(f"step {data['step']:>2} [{data['emotion']}] {data['thought'][:70]}")
        elif event == "session_done":
            r = data["result"]
            log.append(f"=> {OUTCOME_JA.get(r.outcome, r.outcome)} (steps {r.steps}, rage {r.rage_clicks})")
        elif event == "done":
            log.append(f"artifacts: {data['run_dir']}")
        step_lines.code("\n".join(log[-12:]), language=None)

    with status:
        summary, run_dir = run_cohort(
            run_cfg,
            target_url=form["url"],
            task=form["task"],
            context=form["context"],
            count=form["count"],
            progress=progress,
        )
    status.update(label=f"実行完了: {run_dir.name}", state="complete", expanded=False)
    return summary, run_dir


def show_summary(summary) -> None:
    c = summary.cohort
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("完了率", f"{c.completion_rate * 100:.0f}%" if c else "-")
    col2.metric("平均滞在", f"{c.avg_sim_seconds:.0f} 秒" if c else "-")
    col3.metric("平均迷い", f"{c.avg_hesitation_seconds:.1f} 秒" if c else "-")
    col4.metric("怒りクリック", c.total_rage_clicks if c else "-")
    if c:
        st.markdown(f"> **{c.headline}**")
    st.markdown(f"run: `{summary.run_id}` mode: `{summary.mode}`")


def show_cohort_report(run_dir: Path) -> None:
    md = run_dir / "cohort_report.md"
    if md.exists():
        st.markdown(md.read_text(encoding="utf-8"))


def show_persona_details(run_dir: Path) -> None:
    personas = sorted((run_dir / "personas").glob("p*.json"))
    if not personas:
        return
    ids = [p.stem for p in personas]
    chosen = st.selectbox("ペルソナ", ids)
    persona = load_persona(run_dir, chosen)
    report = load_report(run_dir, chosen)
    steps = load_steps(run_dir, chosen)
    if persona is None:
        st.warning("persona not found")
        return

    left, right = st.columns([1, 2])
    with left:
        st.subheader(f"{persona.name} ({persona.age})")
        st.write(persona.archetype)
        t = persona.personality
        st.caption(
            f"ITリテラシー {t.it_literacy:.2f} | 忍耐力 {t.patience:.2f} | "
            f"決断力 {t.decisiveness:.2f} | 注意力 {t.attention_to_detail:.2f} | 不安 {t.anxiety:.2f}"
        )
        st.write(persona.self_description)
        st.caption(f"背景: {persona.context}")
        if persona.give_up_conditions:
            st.write("離脱条件: " + " / ".join(persona.give_up_conditions))
        if report:
            st.progress(report.satisfaction, text=f"満足度 {report.satisfaction:.2f}")
    with right:
        replay_html = run_dir / "sessions" / chosen / "replay.html"
        if replay_html.exists():
            try:
                import streamlit.components.v1 as components

                components.html(replay_html.read_text(encoding="utf-8"), height=720, scrolling=True)
            except Exception as exc:  # noqa: BLE001
                st.warning(f"replay failed to embed: {exc}")
    if report:
        md = run_dir / "reports" / f"{chosen}.md"
        if md.exists():
            st.markdown(md.read_text(encoding="utf-8"))
    with st.expander("行動ログ（JSONL相当）"):
        for s in steps:
            st.write(
                f"step {s.step} · {s.action.type} · {s.action.emotion} · {s.action.thought}"
            )


def show_past_runs() -> None:
    runs = list_runs(config.runs_dir, limit=20)
    if not runs:
        return
    with st.sidebar:
        st.header("過去の実行")
        options = {f"{r.run_id} ({len(r.sessions)}人)": r.run_id for r in runs}
        choice = st.selectbox("開く", list(options))
        if st.button("開く", use_container_width=True):
            st.session_state["viewing_run"] = options[choice]


def main() -> None:
    show_past_runs()
    form = sidebar_run_form()
    if form:
        summary, run_dir = show_live_progress(form)
        st.session_state["viewing_run"] = str(run_dir)
        st.session_state["last_summary"] = json.loads(summary.model_dump_json())

    if "viewing_run" in st.session_state:
        run_dir = Path(st.session_state["viewing_run"])
        if (run_dir / "run.json").exists():
            summary = st.session_state.get("last_summary")
            st.divider()
            st.subheader(f"結果: {run_dir.name}")
            if summary:
                show_summary(summary)
            show_cohort_report(run_dir)
            show_persona_details(run_dir)
        else:
            st.error(f"run not found: {run_dir}")


if __name__ == "__main__":
    main()

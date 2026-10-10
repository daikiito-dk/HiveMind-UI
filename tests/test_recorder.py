"""Recorder tests: artifacts, replay HTML, GIF."""

import io

from PIL import Image

from hivemind.recorder import SessionRecorder
from hivemind.schemas import Action, Persona, PersonaTraits, SessionResult, StepRecord


def make_persona() -> Persona:
    return Persona(
        id="p1", archetype="テスト", name="テスト花子", age=33, language="ja",
        personality=PersonaTraits(it_literacy=0.4, patience=0.5, decisiveness=0.5, attention_to_detail=0.5, anxiety=0.4),
        goal="振込を完了する",
    )


def tiny_png(w: int = 320, h: int = 200, color: tuple = (240, 240, 240)) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_result() -> SessionResult:
    return SessionResult(
        persona_id="p1", outcome="completed", steps=2, sim_ms=5000, wall_ms=1000,
        hesitation_ms=1200, rage_clicks=0, pages_visited=2, finish_verdict="完了した。",
    )


def test_recorder_writes_artifacts(tmp_path):
    run_dir = tmp_path / "runs" / "r1"
    recorder = SessionRecorder(run_dir, make_persona())
    shot1 = recorder.save_shot(1, tiny_png())
    shot2 = recorder.save_shot(2, tiny_png(color=(250, 200, 200)))
    for step, shot, emotion in (
        (1, shot1, "confused"),
        (2, shot2, "satisfied"),
    ):
        recorder.step(
            StepRecord(
                step=step, t_ms=step * 2000,
                action=Action(type="click", element=step - 1, thought=f"思考{step}", emotion=emotion),
                url="https://example.test", title="Example", shot=shot,
                cursor=(100, 100), url_after="https://example.test/next", result_ok=True, result_note="clicked「OK」",
            )
        )
    result = make_result()
    recorder.finalize(result, make_persona())
    session_json = (recorder.dir / "session.json").read_text(encoding="utf-8")
    assert "テスト花子" in session_json and "completed" in session_json
    assert len((recorder.dir / "log.jsonl").read_text(encoding="utf-8").splitlines()) == 2

    html_path, gif_path = recorder.build_replays(result)
    html = html_path.read_text(encoding="utf-8")
    assert "HiveMind replay" in html
    assert "data:image/png;base64" in html
    assert "思考1" in html and "思考2" in html
    assert "OUTCOME" in html  # final outcome card frame
    assert gif_path is not None and gif_path.exists() and gif_path.stat().st_size > 0


def test_recorder_gif_skips_missing_shots(tmp_path):
    run_dir = tmp_path / "runs" / "r2"
    recorder = SessionRecorder(run_dir, make_persona())
    recorder.step(
        StepRecord(
            step=1, t_ms=1000, action=Action(type="wait", seconds=1, thought="...", emotion="bored"),
            url="https://example.test", shot=None,
        )
    )
    recorder.finalize(make_result(), make_persona())
    html_path, gif_path = recorder.build_replays(make_result())
    assert html_path.exists()
    # no shots at all -> no gif
    assert gif_path is None

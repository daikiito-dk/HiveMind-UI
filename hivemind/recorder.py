"""Session artifacts: JSONL step log, screenshots, self-contained HTML replay, GIF.

The HTML replay is a single file (screenshots embedded as base64) with an
animated cursor and the persona's inner monologue as subtitles - the asset you
post on X. The GIF is a compressed, text-free version for timelines.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from PIL import Image, ImageDraw

from .schemas import Persona, SessionResult, StepRecord

EMOTION_COLORS: dict[str, str] = {
    "neutral": "#9e9e9e",
    "curious": "#42a5f5",
    "confident": "#66bb6a",
    "confused": "#fdd835",
    "frustrated": "#fb8c00",
    "angry": "#e53935",
    "satisfied": "#43a047",
    "bored": "#bdbdbd",
    "anxious": "#8e24aa",
}

OUTCOME_JA = {
    "completed": "タスク完了",
    "gave_up": "離脱（キレ）",
    "timeout": "時間切れ",
    "error": "エラー",
}


def _fmt_ms(ms: int) -> str:
    m, s = divmod(ms // 1000, 60)
    return f"{m}:{s:02d}"


class SessionRecorder:
    """Writes everything for one persona session under run_dir/sessions/<persona_id>/."""

    def __init__(self, run_dir: Path, persona: Persona) -> None:
        self.run_dir = run_dir
        self.persona = persona
        self.dir = run_dir / "sessions" / persona.id
        self.shots_dir = self.dir / "shots"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "log.jsonl"
        self.steps: list[StepRecord] = []

    # -------------------------------------------------------------- writing
    def save_shot(self, step: int, png: bytes) -> str:
        fname = f"step_{step:03d}.png"
        (self.shots_dir / fname).write_bytes(png)
        return f"shots/{fname}"

    def step(self, rec: StepRecord) -> None:
        self.steps.append(rec)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(rec.model_dump_json() + "\n")

    def finalize(self, result: SessionResult, persona: Persona) -> None:
        payload = {
            "persona": json.loads(persona.model_dump_json()),
            "result": json.loads(result.model_dump_json()),
        }
        (self.dir / "session.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -------------------------------------------------------------- replays
    def build_replays(self, result: SessionResult, gif: bool = True) -> tuple[Path, Path | None]:
        html_path = self.dir / "replay.html"
        html_path.write_text(self._replay_html(result), encoding="utf-8")
        gif_path = None
        if gif:
            built = self._replay_gif()
            if built is not None:
                gif_path = built
        return html_path, gif_path

    def _frames(self, result: SessionResult) -> list[dict]:
        frames: list[dict] = []
        last_cursor = (50, 50)
        vw, vh = 1280, 800
        for rec in self.steps:
            shot_path = self.shots_dir / Path(rec.shot).name if rec.shot else None
            img = None
            if shot_path and shot_path.exists():
                img = "data:image/png;base64," + base64.b64encode(shot_path.read_bytes()).decode()
                with Image.open(shot_path) as im:
                    vw, vh = im.size
            if rec.cursor:
                last_cursor = rec.cursor
            px = round(last_cursor[0] / vw * 100, 2)
            py = round(last_cursor[1] / vh * 100, 2)
            frames.append(
                {
                    "img": img or "",
                    "px": px,
                    "py": py,
                    "t": rec.t_ms,
                    "thought": rec.action.thought,
                    "emotion": rec.action.emotion,
                    "color": EMOTION_COLORS.get(rec.action.emotion, "#9e9e9e"),
                    "action": rec.action.type,
                    "label": (rec.result_note or "")[:90],
                    "step": rec.step,
                }
            )
        # outcome card as the final frame
        frames.append(
            {
                "img": frames[-1]["img"] if frames else "",
                "px": 50,
                "py": 50,
                "t": (self.steps[-1].t_ms + 2000) if self.steps else 0,
                "thought": f"OUTCOME: {OUTCOME_JA.get(result.outcome, result.outcome)} - "
                f"{result.finish_verdict or result.gave_up_reason or ''}",
                "emotion": "satisfied" if result.outcome == "completed" else "angry",
                "color": "#43a047" if result.outcome == "completed" else "#e53935",
                "action": "outcome",
                "label": f"steps {result.steps} / rage clicks {result.rage_clicks}",
                "step": len(self.steps) + 1,
            }
        )
        return frames

    def _replay_html(self, result: SessionResult) -> str:
        frames = self._frames(result)
        p = self.persona
        title = f"HiveMind replay - {p.name} ({p.archetype})"
        meta = (
            f"{OUTCOME_JA.get(result.outcome, result.outcome)} | steps {result.steps} | "
            f"sim {_fmt_ms(result.sim_ms)} | hesitation {_fmt_ms(result.hesitation_ms)} | "
            f"rage clicks {result.rage_clicks} | mode {p.generated_by}"
        )
        header = (
            f"{p.name}, {p.age} - {p.archetype} - "
            f"IT literacy {p.personality.it_literacy:.2f}, patience {p.personality.patience:.2f}"
        )
        html = REPLAY_TEMPLATE
        html = html.replace("__TITLE__", title).replace("__META__", meta).replace("__HEADER__", header)
        html = html.replace("__FRAMES__", json.dumps(frames, ensure_ascii=False))
        return html

    def _replay_gif(self, width: int = 800) -> Path | None:
        if not self.steps:
            return None
        imgs: list[Image.Image] = []
        durations: list[int] = []
        last_cursor = (50, 50)
        for idx, rec in enumerate(self.steps):
            shot_path = self.shots_dir / Path(rec.shot).name if rec.shot else None
            if not shot_path or not shot_path.exists():
                continue
            img = Image.open(shot_path).convert("RGB")
            scale = width / img.width
            img = img.resize((width, max(1, int(img.height * scale))))
            if rec.cursor:
                last_cursor = rec.cursor
            draw = ImageDraw.Draw(img)
            cx, cy = int(last_cursor[0] * scale), int(last_cursor[1] * scale)
            draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=(229, 57, 53), outline=(255, 255, 255), width=2)
            draw.text((10, 8), f"step {rec.step} - {rec.action.emotion}", fill=(20, 20, 20))
            next_t = self.steps[idx + 1].t_ms if idx + 1 < len(self.steps) else rec.t_ms + 1600
            durations.append(max(300, min(1600, next_t - rec.t_ms)))
            imgs.append(img)
        if not imgs:
            return None
        out = self.dir / "replay.gif"
        imgs[0].save(
            out,
            save_all=True,
            append_images=imgs[1:],
            duration=durations,
            loop=0,
            optimize=True,
        )
        return out


REPLAY_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  body { background:#0e1116; color:#e8e8e8; font-family:'Hiragino Sans','Noto Sans JP',system-ui,sans-serif; margin:0; }
  header { padding:14px 20px 6px; max-width:1280px; margin:0 auto; }
  header h1 { font-size:18px; margin:0; }
  header .sub { color:#9aa3b2; font-size:13px; margin-top:4px; }
  #stage { position:relative; max-width:1280px; margin:0 auto; }
  #frame { width:100%; display:block; border-radius:8px; background:#1a1f29; }
  #cursor { position:absolute; width:16px; height:16px; border-radius:50%;
            background:rgba(229,57,53,.9); border:2px solid #fff; transform:translate(-50%,-50%);
            transition:left .5s cubic-bezier(.4,0,.2,1), top .5s cubic-bezier(.4,0,.2,1); pointer-events:none; }
  #bubble { position:absolute; left:2%; right:2%; bottom:3%; background:rgba(14,17,22,.93);
            border-left:4px solid var(--c,#e53935); padding:10px 14px; border-radius:8px; font-size:15px; }
  #emotion { position:absolute; top:10px; right:10px; padding:3px 12px; border-radius:14px;
             font-size:12px; font-weight:700; color:#0e1116; background:var(--c,#e53935); }
  #controls { max-width:1280px; margin:14px auto 30px; display:flex; gap:12px; align-items:center; padding:0 20px; }
  button, select { background:#1c2129; color:#e8e8e8; border:1px solid #333c4d; border-radius:6px; padding:6px 16px; cursor:pointer; }
  input[type=range] { flex:1; }
  #clock { font-variant-numeric:tabular-nums; color:#9aa3b2; min-width:80px; text-align:right; font-size:14px; }
</style>
</head>
<body>
<header>
  <h1>HiveMind replay - __HEADER__</h1>
  <div class="sub">__META__</div>
</header>
<div id="stage">
  <img id="frame" alt="screenshot">
  <div id="cursor"></div>
  <div id="emotion"></div>
  <div id="bubble"></div>
</div>
<div id="controls">
  <button id="play">▶ play</button>
  <input id="seek" type="range" min="0" value="0">
  <select id="speed">
    <option value="1">1x</option><option value="2">2x</option>
    <option value="4" selected>4x</option><option value="8">8x</option>
  </select>
  <span id="clock">0:00</span>
</div>
<script>
const frames = __FRAMES__;
const els = ["play","seek","speed","clock","frame","cursor","bubble","emotion"].reduce((o, id) => (o[id] = document.getElementById(id), o), {});
let i = 0, timer = null, speed = 4;

els.seek.max = frames.length - 1;

function show(n) {
  i = Math.max(0, Math.min(n, frames.length - 1));
  const f = frames[i];
  if (f.img) els.frame.src = f.img;
  els.cursor.style.left = f.px + "%";
  els.cursor.style.top = f.py + "%";
  els.bubble.textContent = f.thought || "";
  els.bubble.style.setProperty("--c", f.color);
  els.emotion.textContent = f.emotion;
  els.emotion.style.setProperty("--c", f.color);
  els.seek.value = i;
  const m = Math.floor(f.t / 60000), s = Math.floor((f.t % 60000) / 1000);
  els.clock.textContent = m + ":" + String(s).padStart(2, "0");
}

function frameDur(n) {
  if (n + 1 >= frames.length) return 1800;
  const gap = Math.max(400, Math.min(3000, frames[n + 1].t - frames[n].t));
  return gap / speed;
}

function pause() { clearTimeout(timer); timer = null; els.play.textContent = "\\u25b6 play"; }

function play() {
  if (timer) return;
  els.play.textContent = "\\u23f8 pause";
  (function tick() {
    if (i >= frames.length - 1) { show(0); }
    else show(i + 1);
    if (i >= frames.length - 1) { pause(); return; }
    timer = setTimeout(tick, frameDur(i));
  })();
}

els.play.onclick = () => timer ? pause() : play();
els.speed.onchange = () => { speed = parseInt(els.speed.value, 10); };
els.seek.oninput = () => { pause(); show(parseInt(els.seek.value, 10)); };
show(0);
</script>
</body>
</html>
"""

# HiveMind-UI — Architecture

How the three phases fit together, what each data contract carries, and where the
Grok / offline boundary sits. Written to be auditable: every number in a report can
be traced back to a line in a behavior log.

- [1. Pipeline](#1-pipeline)
- [2. Data contracts](#2-data-contracts)
- [3. Phase 1 — persona generation](#3-phase-1--persona-generation)
- [4. Phase 2 — embodied browsing](#4-phase-2--embodied-browsing)
- [5. The offline behavior model](#5-the-offline-behavior-model)
- [6. Phase 3 — review analysis](#6-phase-3--review-analysis)
- [7. Artifacts and the replay format](#7-artifacts-and-the-replay-format)
- [8. Configuration and cost controls](#8-configuration-and-cost-controls)
- [9. Extension points](#9-extension-points)
- [10. Testing strategy](#10-testing-strategy)
- [要約（日本語）](#要約日本語)

---

## 1. Pipeline

`hivemind/runner.py::run_cohort` is the only orchestrator. One run = one cohort:

```
run_cohort(config, target_url, task, context, count)
│
├─ mkdir runs/<timestamp>-<mode>/            personas/  sessions/  reports/
├─ GrokClient(config)                        only when mode == grok
├─ browser_factory = PlaywrightBrowserFactory(config)
│
├─ PHASE 1  generator = make_persona_generator(config, grok)
│           batch = generator.generate(...)         -> personas/batch.json
│
├─ PHASE 2 + 3, sequential, one persona at a time
│   for persona in batch.personas:
│       policy  = GrokActionPolicy(grok, ...) | MockActionPolicy(persona, task, seed)
│       browser = factory.open()                    fresh context, no cookie bleed
│       result, steps = SessionExecutor.run(persona, policy, browser, recorder)
│       browser.close()
│       recorder.build_replays(result, gif=True)    replay.html + replay.gif
│       report = analyst.review(persona, result, steps, shots)
│
└─ PHASE 3  cohort = analyst.summarize_cohort(reports, results, personas)
            run.json  cohort.json  cohort_report.md  usage.json
```

Three properties hold by construction:

1. **Mode resolution happens once** (`Config.resolved_mode`): `mock`, or `grok` when an
   `XAI_API_KEY` is present. Both modes run the identical executor, recorder, and
   analyst interfaces — nothing downstream knows which brain drove the session.
2. **Personas are isolated.** Each gets a new browser context, a new policy object, and
   its own run directory. A rage-quit in `p3` cannot contaminate `p4`.
3. **Failures are contained.** One bad `browser_action` returns `PerformResult(ok=False)`
   instead of raising; a Grok outage mid-cohort ends that session with
   `outcome="error"` and the run still completes with the remaining personas.

---

## 2. Data contracts

All cross-phase types live in `hivemind/schemas.py` (Pydantic v2). They are the API of
the system and are reused directly as LLM output schemas.

| Model | Produced by | Consumed by | Notes |
| --- | --- | --- | --- |
| `PersonaTraits` | Phase 1 | Phase 2 policy | Five 0..1 floats that literally drive behavior |
| `XSignal` | Phase 1 | Phase 1 prompt, reports | `insight` + `evidence[]` from X Search |
| `Persona` | Phase 1 | Phase 2, 3, replays | Identity, context, voice, `give_up_conditions` |
| `PersonaBatch` | Phase 1 | Runner | Structured-output envelope + `x_research_summary` |
| `Element` | Phase 2 (JS) | Phase 2 prompt | `index, tag, kind, label, x, y, w, h` |
| `PageState` | Phase 2 (JS) | Phase 2 prompt | URL, title, elements, text digest, viewport |
| `Action` | Phase 2 policy | Browser + recorder | The only thing the agent can express |
| `StepRecord` | Phase 2 executor | Recorder, analyst | Adds `url_after`, `target_label`, `cursor`, `result_ok` |
| `SessionResult` | Phase 2 executor | Analyst, run.json | `outcome, steps, sim_ms, hesitation_ms, rage_clicks` |
| `Quote` / `FrictionPoint` | Phase 3 | Reports | Verbatim thoughts; area + severity + fix |
| `Report` | Phase 3 | Per-persona report | Satisfaction, NPS-like, would-return, copy feedback |
| `CohortReport` | Phase 3 | Cohort report | Merged friction, drop-reason histogram |
| `RunSummary` | Runner | `cli.py runs`, dashboard | Everything needed to re-open a past run |

`Action.type` is a closed set: `click | type_text | scroll | back | wait | finish | give_up`.
Because the action vocabulary is small and typed, the same schema serves as the Grok
function-calling tool definition (`BROWSER_ACTION_TOOL`), the mock policy's return type,
and the recorder's input.

### Why `sim_ms` and `wall_ms` are separate

The recorder writes two clocks. `wall_ms` is real elapsed time; `sim_ms` is the *human*
time the session would have taken: per-action time (`action_sim_ms`) **plus** the hesitation
before each action, capped at 8s per step. `--speed 6` compresses the demo to a watchable
length without corrupting the reported "this persona spent 47 seconds on this page" claim.

Because hesitation is contained in dwell time, `sim_ms >= hesitation_ms` holds for every
session; `tests/test_agent.py::test_dwell_time_includes_hesitation` enforces it, since
reports print the two numbers side by side and a dwell shorter than the thinking inside it
would be self-contradictory. Tests assert on `sim_ms` so they never depend on machine speed.

---

## 3. Phase 1 — persona generation

`hivemind/persona.py` exposes `make_persona_generator(config, grok)` returning either
`GrokPersonaGenerator` or `MockPersonaGenerator`. Same signature, same `PersonaBatch`.

**Grok path.** One `client.responses.parse(...)` call with
`text_format=PersonaBatch` and the `x_search` server tool attached. The prompt
(`prompts.persona_system_prompt`) forces a specific method: search X from several angles
about *the live context*, extract recurring frustrations, then design N personas that
differ in age/literacy/temperament but share that context. Hard rule: evidence must come
from what search actually returned, never invented.

The X Search tool is date-bounded rather than open-ended:

```python
tool = {"type": "x_search",
        "from_date": (today - timedelta(days=config.search_days)).isoformat(),
        "to_date":   (today + timedelta(days=1)).isoformat()}
# extra_body={"max_turns": config.search_max_turns}   # caps search rounds => caps cost
```

Post-processing is deterministic and cheap: truncate to `count`, assign `p1..pN` ids,
stamp `generated_by="grok"`, default `goal` to the task.

**Mock path.** Five Japanese archetypes (慎重シニア, せっかち会社員, 投資初心者, 大学生,
専業主婦) with hand-tuned trait vectors, recombined with a seeded `random.Random` so a
given `--seed` always yields byte-identical personas. Traits stay in band regardless of
context, which keeps cohort statistics comparable across runs.

The prompt deliberately makes the trait numbers load-bearing: `it_literacy=0.15` is
described as "will genuinely misread the UI and miss small links," `patience=0.1` as
"will bounce quickly when confused." The model is told these are wired to a real browser,
because otherwise it would generate flattering, uniformly-competent testers.

---

## 4. Phase 2 — embodied browsing

`SessionExecutor.run` (`hivemind/agent.py`) is browser-agnostic:

```
for step in 1..max_steps:
    state     = browser.state()                 # elements + digest
    shot      = browser.screenshot()            # PNG bytes
    action    = policy.decide(persona, state, steps, shot)
    sleep(hesitate_ms / speed)                  # hesitation is visible, not hidden
    perform   = browser.perform(action, state)
    recorder.step(StepRecord(...))              # append to log.jsonl
    break on finish | give_up | budget exceeded
```

### The hands (`hivemind/browser.py`)

`PlaywrightBrowserFactory` starts one Chromium process per cohort and opens a fresh
context per persona (own cookies, `locale="ja-JP"`, 1280×800). Element extraction is a
single JS pass over `a, button, input, select, textarea, [role=button], [onclick]`,
keeping only on-screen nodes (capped at 80), deriving a label from
`aria-label → innerText → value/placeholder/title/alt → tag`. A 1500-character
`innerText` digest accompanies it so the agent can read copy it cannot click.

**Everything is coordinate-first.** Clicks are `mouse.move(cx, cy)` + `mouse.click`, never
`element.click()`. This matters for two reasons: it produces a faithful cursor trace for
the replay, and it reproduces the real failure mode where a human clicks slightly wrong.
`type_text` triple-clicks first so typing *replaces* existing content — without it, a
pre-filled field ends up as `30000テ30000テストスト`, which is what users actually complain
about but not what we want to record as a *bot* bug. `back` guards against landing on
`about:blank` and returns `ok=False` ("first page") instead of letting a persona exit the
site.

### The brain (`hivemind/agent.py::GrokActionPolicy`)

`prompts.agent_system_prompt` dumps the full persona JSON, the task, and three hard rules:

- **Skill level is sacred.** The persona is not secretly smart and may not use knowledge
  it would not have.
- **Patience is sacred.** Emotion escalates `confused → frustrated → angry`; the persona's
  own `give_up_conditions` are quoted back at it, and `give_up(note)` carries last words.
- **`thought` is the subtitle.** First person, in persona voice, in persona language,
  because it is rendered as the replay caption. "What is 振込(通常払込み)?? Just say TRANSFER."

Per turn the model gets a `step_brief`: step number, simulated elapsed time, URL, title,
numbered element list, text digest, and the last ten action/result pairs. It must return
**exactly one** `browser_action` tool call.

That contract is enforced, not hoped for. Grok will happily answer with the action as JSON
*text* in a message if allowed to, which silently produced `outcome="error"` on step 1 of
every live session until it was caught, so the request sets `tool_choice="required"`. As a
second line of defence `_action_from_text` recovers an action from message text (including
inside a markdown fence) whenever no function call comes back, and only if both fail does
`decide_action` retry once with an explicit nudge and then raise `GrokError` — a
session-ending error rather than a silent fallback, because a persona that stops obeying the
action grammar is a bug worth surfacing.

Rage clicks are not a model output. `count_rage_clicks` counts consecutive `angry`
clicks in the recorded steps, so the metric stays auditable when the brain is Grok.

---

## 5. The offline behavior model

`hivemind/behaviors.py::MockActionPolicy` exists so the whole pipeline runs with zero API
keys, in CI, and in demos, deterministically. It is a transparent model, not a stub —
every trait maps to a visible mechanism:

| Trait | Mechanism |
| --- | --- |
| `it_literacy` | Misfire probability `0.55 * (1 - literacy) ** 1.4` — the persona clicks a plausible-but-wrong target; scales hesitation; scales how much unfamiliar vocabulary hurts |
| `patience` | Rage threshold at `2.0 + 5.0 * patience` accumulated frustration; also `3 + int(6 * patience)` consecutive fruitless steps before leaving, and a `6 + int(8 * patience)` dead-click backstop |
| `anxiety` | Extra hesitation and "wait and re-read" actions; second-guessing thoughts; gravitates to the official-looking login form |
| `decisiveness` | Lower hesitation |
| `attention_to_detail` | Occasional pauses on fine print |

Frustration accounting is label-keyed, never index-keyed, because element indices shift
when the page scrolls:

- click that reaches a *new* page → `-0.12` (progress feels good), and the stall counter resets
- click that lands on an *already-visited* URL → `+0.34`, marked dead on **both** the page it
  started from and the page it landed on
- click that changes nothing → `+0.32`, marked dead on that page
- reading unfamiliar jargon → up to `+1.0` per page (see below)

**Comprehension is charged, not just clicking.** Only penalising wasted clicks made every
persona complete a hostile site, because a person who reads `被仕向金融機関` and does not
understand it is frustrated whether or not their click "worked". `_read_load` charges
`JARGON_TERM_PENALTY * illiteracy` per unfamiliar term, capped at `JARGON_PAGE_CAP` per page,
never re-charging terms already read on that page, and near-nothing below `LITERACY_COMFORT`.
This is what makes the model discriminate on the real retro bank sample: at literacy 0.15 no
persona finishes, at 0.85 all of them do.

Two rules keep sessions decision-shaped rather than churning. A candidate must score at
least `CLICK_FLOOR = 1.0` keyword hits to be clicked at all, otherwise the persona
scrolls/waits/goes back like a human; and forward controls outrank topic links
(`PROGRESS_BONUS`), because a nav link mentioning `残高` otherwise outscores the form's own
「この内容で実行する」 and the persona ping-pongs between two nav links forever. Goal
keywords come from `GOAL_SYNONYMS` (e.g. `振込` matches `振り込`, `振込み`, `送金`,
`transfer`), so informal Japanese in a task string still resolves.

**Forms are filled and then submitted.** `_maybe_type` puts the persona into a form intent
(`pending_submit_url`, `last_field_y`): it fills the fields nearest the one it just typed,
then `_submit_candidate` presses the control belonging to that form — chosen by proximity to
the last field instead of by score, so nav links can never outbid it. If that button is not
on screen, `_submit_search` scrolls to find it (bounded per URL, so a form whose button is
unreachable cannot trap the persona). Scroll budgets are counted **per URL**
(`self.scrolls`), so revisiting a page cannot restart the same hunt.

Give-up has three routes, all reachable inside a normal session: the frustration threshold
(rage burst, then `give_up`), the exploration streak when nothing scores, and the dead-click
backstop. The backstop exists because without it a patient persona with plenty of tempting
decoy links wanders until the step budget runs out and the run reports a *timeout* — which
reads as an agent that got lost rather than a user who gave up, the opposite of the signal
the product is selling. `_rage_or_give_up` is the single exit: personas with feeling left
take it out on the UI with a short burst of pointless clicks first.

Thought pools (`THOUGHTS_JA` / `THOUGHTS_EN`, `EXPLORE_THOUGHTS_JA/EN`) are per-emotion
templates so mock replays read like a person, not a state machine.

**What the mock is not.** It is not a simulation of intelligence. It cannot judge copy
quality, and its reports say `generated_by: mock`. Its job is to exercise and validate the
machinery — element extraction, coordinate clicks, navigation, recording, reporting — so
that the Grok path is the only untested variable left. The trait→outcome mapping is asserted
statistically (`test_patience_and_literacy_drive_outcomes`), never on a single seed, because
one persona's route through a trap-rich site can get lucky.

---

## 6. Phase 3 — review analysis

`hivemind/analyst.py` provides `MockAnalyst` and `GrokAnalyst` behind one interface.

**The division of labor is the point.** Numbers are computed from the log
(`run_metrics`): completion, `sim_ms`, hesitation totals, rage clicks, drop reasons,
friction counts per UI area. Prose is written by the model: headline, verbatim quotes,
severity, why-it-hurt-*this*-persona, and the fix. A hallucinated statistic cannot enter a
report, because the analyst is never asked to produce one.

**Per persona.** The payload is the persona JSON, the `SessionResult`, and a compact
behavior log (step, `t_ms`, action, target, thought, emotion, `hesitate_ms`, url,
`result_ok`, `result_note`). In Grok mode up to `analyst_shots` screenshots are attached,
ranked by emotional load — `angry` first, then `frustrated`/`confused` (`_key_shots`). The
analyst returns a structured `Report`: satisfaction, NPS-like score, would-return, quotes,
friction points with severity and fixes, and copy feedback, all in the persona's language.

**Per cohort.** All reports plus the computed metrics go into one call producing a
`CohortReport`: merged and ranked friction, the most quotable verbatims, a drop-reason
histogram, and at most five recommendations ordered by expected impact.

Friction points are anchored to a UI *area*, derived from `StepRecord.target_label` — not
from the model's memory of the page and not from the raw typed value. Getting this wrong
early on produced reports blaming the amount field for a problem that was actually the
login button. `FIX_RULES` maps recognizable areas (番号/暗証, FAQ/お知らせ, 口座開設/ローン,
back/wait) to concrete remediation text so even the mock analyst gives actionable advice.

`render_report_markdown` / `render_cohort_markdown` turn both contracts into the
human-facing `.md` files. Reports are per-run snapshots; nothing is rewritten in place.

---

## 7. Artifacts and the replay format

```
runs/20261010-173813-mock/
├── run.json                     RunSummary (mode, model, target, task, sessions, cohort)
├── cohort.json                  Phase 3 aggregate contract
├── cohort_report.md             rendered aggregate report
├── usage.json                   cost, tokens, X-search rounds and posts fetched
├── personas/batch.json          full PersonaBatch incl. x_research_summary
├── personas/p1.json …           individual personas
├── reports/p1.json / p1.md …    per-persona debrief (machine + human)
└── sessions/p1/
    ├── log.jsonl                one StepRecord per line — the source of truth
    ├── shots/step_001.png …     per-step screenshots
    ├── replay.html              self-contained player
    └── replay.gif               X-postable cursor trace
```

`log.jsonl` is append-only and is the only artifact the analyst ever reads. If a report
looks wrong, diff it against the JSONL — there is no other state.

`replay.html` is fully self-contained (no CDN, no network): embedded base64 screenshots,
an animated cursor interpolated between recorded `cursor` coordinates, a subtitle bar that
shows each step's `thought` and emotion, markers for rage clicks, and playback controls
with a 1×/2×/4× speed selector. It is generated by `recorder.build_replays` from the same
`StepRecord` list as the GIF, so the two can never disagree. The GIF is built with Pillow:
one frame per unique screenshot, cursor overlay, duration scaled by `sim_ms` between steps.

---

## 8. Configuration and cost controls

`Config.from_env()` reads `.env`, then applies per-call overrides (`cli.py` flags win).

| Env var | Default | Effect |
| --- | --- | --- |
| `XAI_API_KEY` | — | Present ⇒ `auto` resolves to grok |
| `HIVEMIND_MODE` | `auto` | `auto \| mock \| grok` |
| `HIVEMIND_MODEL` | `grok-4.7` | Model for all three phases |
| `HIVEMIND_MAX_STEPS` | `30` | Per-session step ceiling |
| `HIVEMIND_BUDGET_SECONDS` | `240` | Per-session wall-clock ceiling |
| `HIVEMIND_VISION` | `true` | `false` ⇒ DOM-only loop (cheaper, no screenshot tokens) |
| `HIVEMIND_HEADLESS` | `true` | `false` to watch the browser |
| `HIVEMIND_PERSONAS` | `3` | Default cohort size |
| `HIVEMIND_SEARCH_DAYS` | `7` | How far back Phase 1 may search X |
| `HIVEMIND_SEARCH_MAX_TURNS` | `4` | Search rounds per Phase-1 call (search is billed per post) |
| `HIVEMIND_ANALYST_SHOTS` | `3` | Screenshots attached to each Phase-3 review |

Additional caps live on `Config`: `speed` (replay compression) and `seed` (determinism).

**Cost is measured, not estimated.** Every xAI response carries `cost_in_usd_ticks` — the
exact amount billed, inclusive of server-side tool costs (`1 USD = 10^10 ticks`, per
`docs.x.ai/developers/cost-tracking`) — and `_record_usage` accumulates it alongside tokens,
`x_search_calls` and `x_posts_fetched`. `runner` writes the result to `usage.json`, so a
cohort's price is a number you can read rather than a guess: the live 1-persona run in this
repo cost `$0.299296` across 7 calls and 5 X-search rounds. Note that
`server_side_tool_usage_details` arrives as a plain **dict**, not a Pydantic model; reading
it as an attribute silently recorded zero posts fetched until that was found.

Every call is retried up to three times with exponential backoff. The OpenAI SDK does not
model xAI's server-side tools and warns about the payload on every request; the warning is
suppressed narrowly around the call in `_call`, since the request itself is correct.

---

## 9. Extension points

The two abstractions that matter are already seams:

- **`policy.decide(persona, state, steps, shot) -> Action`** — swap the brain. `GrokActionPolicy`
  and `MockActionPolicy` are interchangeable; a Claude Computer Use driver would be a third
  implementation with the prompt layer unchanged.
- **`browser` interface** (`goto / state / screenshot / perform / close`) — swap the hands.
  `FakeBrowser` (`hivemind/fake_browser.py`) implements it in pure Python over a synthetic
  site graph and gives every one of these tests a fresh site, which is how Phase 2 is
  tested without a real page. `PlaywrightBrowserFactory` is the real one; a
  `browser_factory` argument to `run_cohort` is the injection point.

Both `run_cohort(browser_factory=...)` and the CLI-independent progress callback
(`progress(event, **data)`, used by the CLI for terminal output and by Streamlit for live
updates) exist so the same pipeline drives a terminal, a dashboard, or a test.

Next: parallel cohort execution (async Playwright, one context per worker) is the main
scaling change; today's sequential loop is deliberate, since it makes logs readable and
`--speed` demos coherent.

---

## 10. Testing strategy

`uv run pytest` — 25 tests, no network, no API key. The real-Chromium test skips itself
when Chromium is absent, so CI never needs a browser download.

| File | Covers |
| --- | --- |
| `test_persona.py` | Mock generator determinism, trait bounds, id/goal normalization |
| `test_behaviors.py` | Trait→behavior mapping; jargon friction scales with illiteracy and is charged once; dead ends recorded on both pages; a no-op click is a dead end; filling a form ends by pressing its button |
| `test_agent.py` | Session loop, action arithmetic, rage-click counting, budgets, error containment, dwell ≥ hesitation |
| `test_browser_integration.py` | Real Chromium: extraction, coordinate clicks, hash nav, screenshots |
| `test_recorder.py` | JSONL round-trip, replay HTML, GIF generation |
| `test_pipeline.py` | End-to-end cohort on `FakeBrowser`, artifacts on disk, determinism |
| `test_grok.py` | Request shapes with a stubbed client: X Search params, tool schema, retries |

Two tests are load-bearing. `test_behaviors.py::test_patience_and_literacy_drive_outcomes`
asserts *statistically* across seeds that low-patience personas quit earlier than
high-patience ones; it was originally written against a single seed and failed for a
legitimate reason — one persona's route through the site can get lucky — so it asserts the
distribution rather than the anecdote. `test_agent.py::test_dwell_time_includes_hesitation`
guards the reported numbers, because an inconsistent pair in a customer-facing report
undermines every other figure in it.

The trait→outcome mapping was also measured against the **real** retro bank sample (5 seeds
per cell, real Chromium), which is the evidence behind the discrimination claim:

| it_literacy | patience | completed |
| --- | --- | --- |
| 0.15 | 0.1 / 0.5 / 0.85 | 0/5 · 0/5 · 0/5 |
| 0.45 | 0.1 / 0.5 / 0.85 | 0/5 · 2/5 · 3/5 |
| 0.85 | 0.1 / 0.5 / 0.85 | 5/5 · 5/5 · 5/5 |

44% overall, no timeouts. Skill decides whether this site's jargon wall is passable at all;
patience decides the middle. That is a claim the demo can make honestly, and it is the kind
of number a prospect can check by re-running one command.

---

## 要約（日本語）

HiveMind-UIは、`runner.run_cohort` 1関数が全工程を統括します。

- **フェーズ1**: Grokの Responses API に `x_search`（日付窓を設定）＋構造化出力を付け、
  「今のX」に根拠を持つペルソナを生成。`PersonaTraits` の5つの0〜1の数値が、そのまま
  フェーズ2の挙動を決めます。APIキーがなければ、同じインターフェースの
  モック生成器（5つの日本語アーキタイプ、seed固定で再現可能）に切り替わります。
- **フェーズ2**: 1ターンにつき「要素リスト＋テキスト＋スクリーンショット」を渡し、
  `browser_action` を1件だけ返させます。操作はすべて**座標ベース**（人間の動きの再現）。
  ペルソナごとに独立したブラウザコンテキストを使い、cookie等の混線を防ぎます。
  記録される `sim_ms` は「人間なら何秒かかったか」で、`--speed` は再生だけを圧縮します。
- **オフラインモデル**: ITリテラシー→誤クリック確率 `0.55*(1-lit)^1.4`、
  忍耐力→怒り閾値 `3.0+10*patience` と離脱までの探索回数 `3+int(6*patience)`。
  フラストレーションは**ラベル基準**で加算（インデックスはスクロールでずれるため）。
- **フェーズ3**: **数値はログから計算し、文章だけをGrokが書く**。満足度・NPS・離脱理由は
  ログ由来なので幻覚が混入しません。摩擦点は `target_label` を起点にUI領域へ紐付けます。
- **成果物**: `log.jsonl` が唯一の真実で、`replay.html`（カーソル＋心の声の字幕、
  ネットワーク不要の自己完結HTML）と `replay.gif` は同じ記録から生成されるため矛盾しません。
- **コスト制御**: X Searchの検索窓とターン数、スクリーンショット送信の有無、ステップ数・
  時間予算をすべて設定で制御し、`usage.json` に呼び出し回数とトークンを記録します。

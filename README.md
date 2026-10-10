# HiveMind-UI

**English** | [日本語](#日本語)

*Real-time persona-linked autonomous UI testing agent — powered by Grok × Computer Use.*

HiveMind-UI replaces qualitative UX research (interviews, usability tests, paid panels) with a pipeline that **generates live personas from what X is talking about right now**, **embodies them in a real browser**, and **writes the subjective debrief** — at cohort scale, in minutes.

```
Phase 1  Persona     Grok + X Search -> dynamic, context-aware personas
                      ("a 30-something who turned frugal after this morning's news")
Phase 2  Execute     The persona drives a real browser via a Computer Use loop.
                      Low IT literacy genuinely misreads the UI. Low patience genuinely rage-quits.
Phase 3  Review     Operation logs, hesitation, screenshots -> interview-style
                      qualitative report + cohort synthesis (all through Grok)
```

```
[X posts, trends, sentiment]
        │  x_search (xAI Responses API)
        ▼
[ Phase 1 ] Persona ── traits: it_literacy, patience, anxiety... (0..1)
        │
        ▼
[ Phase 2 ] Agentic loop ── screenshot + element list ──> Grok -> one Action per turn
        │                    (click / type / scroll / back / wait / finish / give_up)
        │                    hesitation, rage clicks, give-up: all recorded
        ▼
[ Phase 3 ] Grok re-reads the behavior log -> quotes, friction points, fixes
        │
        ▼
runs/<id>/  replay.html (animated cursor + inner monologue) / replay.gif / reports
```

## Why this only works with Grok

- **Live context is the product.** Personas built from stale training data are generic. Persona built from *this morning's X* reacts to *this morning's landing page* — the claim "only possible with Grok" is the demo.
- **Labor automation.** Recruiting, screening, and moderating 100 human testers takes weeks. A context-differentiated cohort of 100 personas takes minutes. Think Optimus for research work.
- **Built for X's ad ecosystem.** Test a landing page against the audience that is trending *right now* before you spend a single ad dollar.

Full strategy: [docs/STRATEGY.md](docs/STRATEGY.md). Technical deep-dive: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quickstart (works with zero API keys)

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), and Chromium (installed automatically).

```bash
make setup    # uv sync + playwright install chromium + .env

# 3 personas attack a deliberately-terrible 1998-style bank site (offline mock)
make demo
```

Every persona session produces:

```
runs/20261010-…-mock/
├── run.json                      cohort summary
├── personas/p1.json              the persona (traits, context, X signals)
├── sessions/p1/
│   ├── log.jsonl                 every action: thought, emotion, hesitation
│   ├── shots/step_001.png …      screenshots
│   ├── replay.html               self-contained replay: animated cursor + subtitles
│   └── replay.gif                X-postable cursor-trace GIF
├── reports/p1.md                 Phase 3 debrief in the persona's voice
└── cohort_report.md              cross-persona synthesis
```

The mock brain is a transparent behavioral model (seeded, reproducible): misfires scale with `1 - it_literacy`, jargon comprehension is charged as frustration, and rage-quits scale with accumulated frustration vs `patience`. It exists so the whole pipeline runs offline, in CI, and in demos — swap to Grok for the real thing. Measured on the retro sample with a real browser, it discriminates by trait: at `it_literacy` 0.15 nobody finishes the task, at 0.85 everybody does.

## Live mode (Grok + X Search)

```bash
cp .env.example .env    # add XAI_API_KEY from https://console.x.ai
uv run python cli.py run \
  --url "https://your-landing-page.example" \
  --task "Sign up for the free trial" \
  --context "Your ad is trending among 20-somethings complaining about rent" \
  --personas 4 --mode grok
```

- Phase 1 uses the xAI **X Search** tool (`x_search`) with a rolling date window, so personas carry evidence (post gists/handles) from X.
- Phase 2 sends a numbered element list + screenshot per turn; Grok returns one strict-schema `browser_action`. Hesitation is replayed in real time.
- Phase 3 uses structured outputs (Pydantic) for machine-readable reports.

This path is validated end to end against the live API, not just unit-tested: a 1-persona run
generated a persona whose `x_signals` quote real posts, drove Chromium as that persona, and
produced a debrief quoting its own behavior log verbatim.

Cost controls: X Search rounds are capped (`HIVEMIND_SEARCH_MAX_TURNS`), screenshot input can be disabled (`HIVEMIND_VISION=false`), per-session step/time budgets are enforced, and token/tool usage is written to `runs/<id>/usage.json`. That file also carries `cost_usd`, the exact amount xAI billed, taken from the `cost_in_usd_ticks` field of every response (see [xAI cost tracking](https://docs.x.ai/developers/cost-tracking)). The live 1-persona validation run in this repo cost $0.30.

## Dashboard

```bash
make app      # Streamlit dashboard at http://localhost:8501
```

Configure a cohort, watch personas think/live, open animated replays, and read reports — or browse past runs.

## Viral demo assets (Build in Public)

```bash
make viral   # scripted spicy cohort -> replay GIFs + suggested X post copy (JA/EN)
```

## CLI reference

```
uv run python cli.py run      --url ... --task ... [--personas N] [--mode mock|grok|auto] [--speed 3] [--seed 42]
uv run python cli.py runs                 # list past runs
uv run python cli.py report  <run_id>     # print cohort report
uv run python cli.py persona <run_id> p1  # one persona's debrief
uv run python cli.py replay  <run_id> [p1]
```

## Repository layout

| Path | What it is |
| --- | --- |
| `hivemind/prompts.py` | All LLM prompts (persona architect, embodiment rules, analyst) |
| `hivemind/grok.py` | xAI Responses API wrapper: X Search, function calling, structured outputs |
| `hivemind/persona.py` | Phase 1: Grok + X Search generator, offline archetype generator |
| `hivemind/agent.py` | Phase 2: the session executor (browser-agnostic loop) |
| `hivemind/behaviors.py` | The offline persona-conditioned behavior model |
| `hivemind/browser.py` | Playwright hands: element extraction, coordinate clicks, screenshots |
| `hivemind/analyst.py` | Phase 3: per-persona review + cohort synthesis (mock & Grok) |
| `hivemind/recorder.py` | JSONL logs, self-contained HTML replay, GIF |
| `hivemind/runner.py` | Cohort orchestration and run artifacts |
| `samples/retro_bank.html` | Deliberately terrible 1998-style bank site (demo target) |
| `samples/modern_bank.html` | The same flow, redesigned — for old-vs-new comparisons |
| `demo_tools/viral_demo.py` | Generates GIFs + X post copy for a scripted cohort |

## Roadmap

- [ ] Parallel cohort execution (async Playwright, 10k-persona scale)
- [ ] Claude Computer Use as an alternative Phase-2 driver (the prompt layer stays Grok)
- [ ] Session recording as interactive HTML with click-heatmaps across a cohort
- [ ] X API publishing of report cards (quoted-post format)
- [ ] Fine-grained trait -> behavior mapping learned from real UX study logs

## Honesty rules

- Mock-mode personas and reports are explicitly labeled `generated_by: mock`; their X signals say so too.
- Phase 3 numbers (completion, hesitation, rage clicks) are computed from the behavior log, not hallucinated by the model — Grok writes the prose, the data writes the numbers.
- Tests run offline and assert deterministic behavior for fixed seeds.

## License

MIT — see [LICENSE](LICENSE).

---

# 日本語

**[English](#english)** | 日本語

*リアルタイム・ペルソナ連動型 自律UIテストエージェント — Grok × Computer Use。*

HiveMind-UIは、定性UXリサーチ（インタビュー、ユーザビリティテスト、モニター募集）を、**今まさにXで話題になっていることからペルソナを生成**し、**その人格で実際のブラウザを操作**し、**主観レビューとしてレポート化**するパイプラインで置き換えます。

- **フェーズ1**: Grok + X Search で「今朝のニュースを見て節約志向が高まっている30代」のような、コンテキスト付きの動的ペルソナを生成
- **フェーズ2**: ペルソナのITリテラシー・忍耐力どおりに、実際のWebサイトをComputer Useループで操作。迷い・誤クリック・離脱も忠実に再現
- **フェーズ3**: 操作ログ・スクリーンショット・滞在時間をGrokが解析し、ペルソナ視点のインタビュー結果と改善レポートを出力

## なぜGrokでしか成立しないか

- **鮮度がプロダクト。** 学習データで作ったペルソナは無味乾燥。今朝のXから作ったペルソナは、今朝のランディングページに今朝の気分で反応する。
- **労働の完全自動化。** 人間100人のテスター調達は数週間。コンテキスト違いの100体ペルソナは数分。
- **X広告主向けキラーツール。** 出稿前に、今バズっている層のLPの刺さりを検証できる。

## はじめ方（APIキー不要で動作）

```bash
make setup    # 依存インストール + Chromium + .env
make demo     # 1998年風の銀行サイトに3ペルソナが挑む（オフライン・モック）
```

各セッションは `replay.html`（カーソルの動き＋心の声つきリプレイ）、`replay.gif`（X投稿用）、行動ログ、定性レポートを書き出します。

## ライブモード（Grok + X Search）

```bash
cp .env.example .env   # XAI_API_KEY を設定
uv run python cli.py run --url "https://…" --task "無料トライアルに登録する" \
  --context "家賃の高さに不満の20代がバズっている直後" --personas 4 --mode grok
```

フェーズ1では X Search で直近のX投稿を実際に検索し、ペルソナに根拠（引用）を持たせます。フェーズ2では要素リスト＋スクリーンショットから Grok が厳密スキーマの `browser_action` を1件ずつ返し、フェーズ3では構造化出力でレポート化します。利用量は `runs/<id>/usage.json` に記録され、検索回数・スクリーンショット送信・ステップ数は設定で制御できます。

## その他

```bash
make app      # Streamlitダッシュボード
make viral    # バズ前提のデモ素材（GIF + X投稿文面 自動生成）
make test     # オフラインテスト（ブラウザ・API不要）
```

構成と戦略の詳細: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) / [docs/STRATEGY.md](docs/STRATEGY.md)

## ライセンス

MIT — [LICENSE](LICENSE)

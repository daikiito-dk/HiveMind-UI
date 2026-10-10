"""Phase 1 - Real-time persona generation.

Two interchangeable generators sharing one interface:
- GrokPersonaGenerator: researches live X sentiment via the x_search tool, then builds
  personas grounded in what it actually found (the "only Grok can do this" path).
- MockPersonaGenerator: seeded archetype recombination, fully offline. The run context
  still flows into the persona so demos feel current; all signals are labeled synthetic.
"""

from __future__ import annotations

import random

from .config import Config
from .grok import GrokClient, GrokError
from .schemas import Persona, PersonaBatch, PersonaTraits, XSignal

MOCK_CONTEXT_DEFAULT = "直近のX上のトレンドを反映（オフライン・モック実行）"


class GrokPersonaGenerator:
    """Powered by Grok + X Search."""

    def __init__(self, client: GrokClient) -> None:
        self.client = client

    def generate(self, context: str, task: str, count: int, language: str = "ja", seed: int | None = None) -> PersonaBatch:
        return self.client.generate_personas(context=context, task=task, count=count, language=language)


# --------------------------------------------------------------------- mock
ARCHETYPES: list[dict] = [
    {
        "key": "z-impatient",
        "archetype": "Z世代・即断即離型",
        "names": ["ゆうた", "かな", "りく", "もえ"],
        "ages": (19, 24),
        "occupations": ["大学生", "アルバイト・フリーター", "新卒1年目"],
        "base": PersonaTraits(it_literacy=0.8, patience=0.12, decisiveness=0.9, attention_to_detail=0.25, anxiety=0.15),
        "frustrations": ["3秒待たされるだけで離脱したくなる", "説明文が長い", "フォーム入力が面倒", "冗長な注意書き"],
        "voice": "砕けた若者言葉。「マジで無理」「それな」「〜じゃん」が出る。思考は短くテンポが速い。",
        "self_description": "TikTokで買い物を済ませる世代。クリック3回で目的地に着けないサイトは全部「古い」。",
        "give_up": ["3ステップ連続で目的のボタンが見つからない", "読み込みやフォームで2回イライラが重なる"],
    },
    {
        "key": "context-sensitive-30s",
        "archetype": "時事コンテキスト感応型30代",
        "names": ["美咲", "健太", "さやか", "亮"],
        "ages": (29, 38),
        "occupations": ["会社員（営業）", "会社員（事務）", "子育て中のワーカー"],
        "base": PersonaTraits(it_literacy=0.6, patience=0.45, decisiveness=0.5, attention_to_detail=0.6, anxiety=0.35),
        "frustrations": ["損をした気分になる表示", "手数料の説明が後出しされる", "比較のための情報が見つからない"],
        "voice": "普通の話し方。感情は控えめだが、核心を突いた皮肉を言う。SNSで静かに炎上させるタイプ。",
        "self_description": "朝のニュースとSNSの空気で行動が変わる。今日の自分の関心事と無関係なキャッチコピーは右から左へ流す。",
        "give_up": ["目的の操作が5ステップを超える", "表示が自分の今の関心事とズレていると感じる"],
    },
    {
        "key": "it-averse-senior",
        "archetype": "IT慎重シニア",
        "names": ["和夫", "洋子", "正", "富子"],
        "ages": (66, 76),
        "occupations": ["退職者", "自営業（引退）", "主婦"],
        "base": PersonaTraits(it_literacy=0.12, patience=0.6, decisiveness=0.25, attention_to_detail=0.7, anxiety=0.85),
        "frustrations": ["小さい文字が読めない", "専門用語が分からない", "押したら取り返しがつかない気がする", "戻り方が分からない"],
        "voice": "丁寧語で独り言が多い。「これは押していいのかしら」「あら、また元に戻ってしまったわ」。",
        "self_description": "窓口なら10分で済む用事が、ネットだと思うだけで緊張する。家族に聞けば孫が「じいじそれは広告だよ」と笑う。",
        "give_up": ["知らない言葉が2回出てきた", "押したら画面が変わって戻り方が分からなくなった", "入力欄が多いと感じた"],
    },
    {
        "key": "skeptic-researcher-40s",
        "archetype": "懐疑的比較検討型40代",
        "names": ["彰", "由紀", "浩二", "理恵"],
        "ages": (40, 49),
        "occupations": ["会社員（管理職）", "自営業", "公務員"],
        "base": PersonaTraits(it_literacy=0.7, patience=0.55, decisiveness=0.3, attention_to_detail=0.85, anxiety=0.3),
        "frustrations": ["根拠のない断り文句", "隠れ手数料", "「簡単」しか書いていない説明"],
        "voice": "理屈っぽい。数字と条件を確認しながら進む。「本当に？」「この条件、どこに書いてある？」。",
        "self_description": "契約する前に3社は比較する。表示の裏を取ろうとして深読みし、却って迷子になることもある。",
        "give_up": ["条件や手数料の記載が見つからない", "同じ画面を2周して進めなくなった"],
    },
    {
        "key": "time-starved-parent",
        "archetype": "時短至上主義の子育て世代",
        "names": ["ちはる", "大輔", "なつみ", "琢磨"],
        "ages": (33, 42),
        "occupations": ["会社員（時短勤務）", "看護師", "フリーランス"],
        "base": PersonaTraits(it_literacy=0.75, patience=0.3, decisiveness=0.7, attention_to_detail=0.45, anxiety=0.4),
        "frustrations": ["子どもが泣いたら終わり", "確認メールが届かない", "途中で中断したら最初からやり直しさせる"],
        "voice": "早口で効率重視。「手間」「まだあるの？」が口癖。思考は常に対話に聞こえる。",
        "self_description": "操作は子どもの昼寝の15分の間に終わらせないといけない。無駄なステップは許されない。",
        "give_up": ["10分以内に終わらせられない見込みが立った", "子どもをあやすタイミングで2回中断した"],
    },
]


def _jitter(rng: random.Random, value: float, span: float = 0.08) -> float:
    return max(0.0, min(1.0, value + rng.uniform(-span, span)))


class MockPersonaGenerator:
    """Offline archetype recombination. Honest about being synthetic."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config

    def generate(self, context: str, task: str, count: int, language: str = "ja", seed: int | None = None) -> PersonaBatch:
        rng = random.Random(seed if seed is not None else 0)
        context = context or MOCK_CONTEXT_DEFAULT
        personas: list[Persona] = []
        picks = rng.sample(ARCHETYPES, k=min(count, len(ARCHETYPES)))
        while len(picks) < count:  # allow repeats with different names when count > pool
            picks.append(rng.choice(ARCHETYPES))
        for i, arch in enumerate(picks[:count], start=1):
            base = arch["base"]
            traits = PersonaTraits(
                it_literacy=_jitter(rng, base.it_literacy),
                patience=_jitter(rng, base.patience),
                decisiveness=_jitter(rng, base.decisiveness),
                attention_to_detail=_jitter(rng, base.attention_to_detail),
                anxiety=_jitter(rng, base.anxiety),
            )
            personas.append(
                Persona(
                    id=f"p{i}",
                    archetype=arch["archetype"],
                    name=rng.choice(arch["names"]),
                    age=rng.randint(*arch["ages"]),
                    occupation=rng.choice(arch["occupations"]),
                    location=rng.choice(["東京", "大阪", "愛知", "福岡", "北海道"]),
                    context=f"今日の行動背景: {context}",
                    goal=task,
                    language=language,
                    personality=traits,
                    self_description=arch["self_description"],
                    frustrations=rng.sample(arch["frustrations"], k=min(3, len(arch["frustrations"]))),
                    voice=arch["voice"],
                    x_signals=[
                        XSignal(
                            insight=f"「{context}」に関連する投稿が直近で増加傾向にある（オフライン推論）",
                            evidence=["(mock) オフライン生成のシグナルです。Grok + X Search 接続で実データに差し替わります。"],
                        )
                    ],
                    give_up_conditions=rng.sample(arch["give_up"], k=min(2, len(arch["give_up"]))),
                    generated_by="mock",
                )
            )
        return PersonaBatch(
            x_research_summary="(mock) Xリサーチはオフラインのため未実施。ペルソナはアーキタイプから合成されました。",
            personas=personas,
        )


def make_persona_generator(config: Config, grok: GrokClient | None):
    mode = config.resolved_mode()
    if mode == "grok":
        if grok is None:
            raise GrokError("grok mode requires a GrokClient")
        return GrokPersonaGenerator(grok)
    return MockPersonaGenerator(config)

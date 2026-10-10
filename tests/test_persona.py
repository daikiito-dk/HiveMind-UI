"""Phase 1 tests: offline persona generation."""

from hivemind.persona import MockPersonaGenerator
from hivemind.schemas import PersonaBatch


def test_mock_generation_shape():
    batch = MockPersonaGenerator().generate("テスト文脈", "振込を完了する", 3, seed=5)
    assert isinstance(batch, PersonaBatch)
    assert len(batch.personas) == 3
    ids = [p.id for p in batch.personas]
    assert len(set(ids)) == 3
    for p in batch.personas:
        assert p.generated_by == "mock"
        assert "テスト文脈" in p.context
        assert p.goal == "振込を完了する"
        assert p.language == "ja"
        t = p.personality
        for value in (t.it_literacy, t.patience, t.decisiveness, t.attention_to_detail, t.anxiety):
            assert 0.0 <= value <= 1.0
        assert p.give_up_conditions


def test_mock_generation_more_than_pool():
    batch = MockPersonaGenerator().generate("ctx", "振込", 7, seed=1)
    assert len(batch.personas) == 7


def test_deterministic_with_seed():
    a = MockPersonaGenerator().generate("ctx", "振込", 3, seed=42)
    b = MockPersonaGenerator().generate("ctx", "振込", 3, seed=42)
    assert [p.name for p in a.personas] == [p.name for p in b.personas]
    assert [p.personality.patience for p in a.personas] == [p.personality.patience for p in b.personas]
    c = MockPersonaGenerator().generate("different context", "振込", 3, seed=42)
    assert [p.context for p in c.personas] != [p.context for p in a.personas]

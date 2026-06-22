from app.agents.llm_router import _price, PRICING


def test_known_model_pricing():
    assert _price("claude-sonnet-4-6", 1_000_000, 0) == 3.0
    assert _price("claude-sonnet-4-6", 0, 1_000_000) == 15.0


def test_unknown_model_is_free():
    assert _price("made-up-model", 100, 100) == 0.0


def test_pricing_table_shape():
    for model, prices in PRICING.items():
        assert len(prices) == 2
        assert all(isinstance(p, (int, float)) for p in prices)

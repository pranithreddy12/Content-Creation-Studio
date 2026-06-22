from app.services.ingestion.chunker import chunk_text


def test_chunker_handles_empty():
    assert chunk_text("") == []


def test_chunker_splits_long_text():
    text = ". ".join(f"Sentence number {i} is here" for i in range(200))
    chunks = chunk_text(text, target_tokens=200, overlap_tokens=20)
    assert len(chunks) > 1
    assert all(c.tokens > 0 for c in chunks)
    assert chunks[0].ord == 0
    assert chunks[-1].ord == len(chunks) - 1


def test_chunker_preserves_short_text():
    text = "Single short sentence."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].text == "Single short sentence."

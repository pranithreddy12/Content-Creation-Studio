"""Token-budgeted text chunker with sentence-aware overlap."""
from __future__ import annotations

import re
from dataclasses import dataclass

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


@dataclass
class Chunk:
    ord: int
    text: str
    tokens: int


def _rough_tokens(s: str) -> int:
    # 1 token ~ 4 chars for English. Cheap, no tiktoken dependency required.
    return max(1, len(s) // 4)


def chunk_text(text: str, *, target_tokens: int = 600, overlap_tokens: int = 80) -> list[Chunk]:
    text = (text or "").strip()
    if not text:
        return []
    sentences = SENTENCE_RE.split(text)
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    ord_ = 0
    for sent in sentences:
        st = _rough_tokens(sent)
        if buf_tokens + st > target_tokens and buf:
            joined = " ".join(buf).strip()
            chunks.append(Chunk(ord=ord_, text=joined, tokens=_rough_tokens(joined)))
            ord_ += 1
            # build overlap tail
            tail: list[str] = []
            tt = 0
            for s in reversed(buf):
                ts = _rough_tokens(s)
                if tt + ts > overlap_tokens:
                    break
                tail.insert(0, s)
                tt += ts
            buf = tail + [sent]
            buf_tokens = sum(_rough_tokens(s) for s in buf)
        else:
            buf.append(sent)
            buf_tokens += st
    if buf:
        joined = " ".join(buf).strip()
        chunks.append(Chunk(ord=ord_, text=joined, tokens=_rough_tokens(joined)))
    return chunks

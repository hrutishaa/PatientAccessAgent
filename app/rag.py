"""Tiny dependency-free retrieval over clinic policy docs (TF-IDF + cosine).
Swap for embeddings (e.g., text-embedding-3-small + a vector DB) in production."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

KB_DIR = Path(__file__).parent / "kb"
STOP = set(["a", "an", "the", "is", "are", "to", "of", "and", "or", "for", "in", "on", "at", "by", "with", "your", "you", "we", "our", "be", "can", "do", "i", "my", "me", "it", "what", "how", "when"])


def tokenize(text: str) -> list[str]:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]
    out = []
    for t in toks:
        if t.endswith("ing") and len(t) > 5:
            t = t[:-3]
        elif t.endswith("s") and len(t) > 3:
            t = t[:-1]
        out.append(t)
    return out


@dataclass
class Passage:
    title: str
    text: str


def load_passages(kb_dir: Path = KB_DIR) -> list[Passage]:
    passages = []
    for path in sorted(kb_dir.glob("*.md")):
        for block in path.read_text().split("## ")[1:]:
            title, _, body = block.partition("\n")
            passages.append(Passage(title.strip(), body.strip()))
    return passages


class Retriever:
    def __init__(self, passages: list[Passage] | None = None) -> None:
        self.passages = passages or load_passages()
        docs = [Counter(tokenize(p.title + " " + p.title + " " + p.text)) for p in self.passages]
        n = len(docs)
        df = Counter(t for d in docs for t in d)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.vecs = [self._vec(d) for d in docs]

    def _vec(self, counts: Counter) -> dict[str, float]:
        v = {t: c * self.idf.get(t, 0.0) for t, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def search(self, query: str, k: int = 2) -> list[tuple[Passage, float]]:
        q = self._vec(Counter(tokenize(query)))
        scored = [(p, sum(q.get(t, 0) * w for t, w in v.items())) for p, v in zip(self.passages, self.vecs, strict=True)]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

"""Lexical BM25 + RRF for the hybrid retriever."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def bm25_scores(
    query_tokens: list[str],
    corpus: list[list[str]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    n = len(corpus)
    if n == 0 or not query_tokens:
        return [0.0] * n

    doc_len = [len(d) for d in corpus]
    avgdl = (sum(doc_len) / n) or 1.0

    df: Counter = Counter()
    doc_tf: list[Counter] = []
    for d in corpus:
        tf = Counter(d)
        doc_tf.append(tf)
        df.update(tf.keys())

    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in df}
    q_terms = set(query_tokens)

    scores: list[float] = []
    for i, tf in enumerate(doc_tf):
        s = 0.0
        for t in q_terms:
            f = tf.get(t, 0)
            if not f:
                continue
            denom = f + k1 * (1 - b + b * doc_len[i] / avgdl)
            s += idf.get(t, 0.0) * (f * (k1 + 1)) / denom
        scores.append(s)
    return scores


def reciprocal_rank_fusion(rankings: list[list], k: int = 60) -> dict:
    # RRF needs no cross-retriever score calibration (Cormack et al., 2009)
    fused: dict = defaultdict(float)
    for ranking in rankings:
        for rank, item_id in enumerate(ranking):
            fused[item_id] += 1.0 / (k + rank + 1)
    return dict(fused)

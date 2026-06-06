"""Lexical BM25 + RRF fusion — pure, stdlib-only."""
from monogram import bm25


def test_tokenize_is_unicode_aware():
    assert bm25.tokenize("Deploy the Model!") == ["deploy", "the", "model"]
    assert "배포하기" in bm25.tokenize("모델 배포하기")   # Hangul blocks are \w


def test_bm25_ranks_matching_doc_higher():
    corpus = [bm25.tokenize(t) for t in [
        "kubernetes deployment rollout strategy",
        "kimchi stew dinner recipe tonight",
        "deploy a model to production",
    ]]
    scores = bm25.bm25_scores(bm25.tokenize("deploy production"), corpus)
    assert scores[2] > scores[1]      # deploy doc beats the dinner doc
    assert scores[2] > 0.0


def test_bm25_empty_edges():
    assert bm25.bm25_scores([], [["a"]]) == [0.0]
    assert bm25.bm25_scores(["a"], []) == []


def test_rrf_top_in_both_wins():
    fused = bm25.reciprocal_rank_fusion([["a", "b", "c"], ["a", "c", "b"]])
    assert fused["a"] > fused["b"] and fused["a"] > fused["c"]


def test_rrf_rewards_cross_retriever_agreement():
    # 'x' (2nd in list1, 1st in list2) should beat 'y' (1st in list1 only).
    fused = bm25.reciprocal_rank_fusion([["y", "x"], ["x"]])
    assert fused["x"] > fused["y"]

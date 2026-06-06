"""Opt-in cross-encoder reranker live test — downloads ~23MB, gated.

Set MONOGRAM_TEST_RERANK=1 (with onnxruntime+tokenizers+huggingface-hub) to run.
Validated by hand: the deployment doc reranks above unrelated docs.
"""
import os


def test_reranker_orders_relevant_first():
    if os.environ.get("MONOGRAM_TEST_RERANK") != "1":
        return  # opt-in: downloads the model; skipped by default

    from monogram import reranker

    docs = [
        {"text": "kimchi stew dinner recipe for tonight"},
        {"text": "Kubernetes deployment rollout for the ML model in production"},
        {"text": "weather forecast for tomorrow afternoon"},
    ]
    out = reranker.rerank_records("how do I deploy the model to production", docs, k=3)
    assert out, "reranker returned nothing (deps/model missing?)"
    assert "deploy" in out[0]["text"].lower()        # most relevant first
    assert out[0]["_rerank"] > out[-1]["_rerank"]    # monotonic score

"""Opt-in EmbeddingGemma live test — downloads ~300MB, so it's gated.

Set MONOGRAM_TEST_GEMMA=1 (with onnxruntime+tokenizers+huggingface-hub installed)
to run. Validated by hand: a Korean query retrieves EN+KO deployment notes over an
unrelated Korean note, at dim 256, and the int8 roundtrip preserves the ranking.
"""
import asyncio
import os


def test_gemma_crosslingual_retrieval_and_int8_roundtrip():
    if os.environ.get("MONOGRAM_TEST_GEMMA") != "1":
        return  # opt-in: downloads the model; skipped by default

    from monogram import embeddings as E

    docs = [
        "Kubernetes에 모델을 배포하는 방법과 롤아웃 전략",       # deploy (KO)
        "오늘 저녁으로 김치찌개를 맛있게 먹었다",                # dinner (KO)
        "How to deploy a machine learning model to production",   # deploy (EN)
    ]
    query = "프로덕션에 모델 배포하기"                           # deploy to prod (KO)

    dvecs = asyncio.run(E.embed_documents(docs))
    qvec = asyncio.run(E.embed_query(query))

    assert E.resolve_model().startswith("google/embeddinggemma")
    assert len(qvec) == 256                                       # MRL truncation
    q8 = E.quantize_int8(qvec)
    ranked = sorted(range(3), key=lambda i: -E.dot_int8(q8, E.quantize_int8(dvecs[i])))
    assert ranked[0] in (0, 2)                                    # a deploy doc, not dinner

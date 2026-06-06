"""EmbeddingGemma backend — pure prompt/pool logic + routing (no onnxruntime)."""
from monogram import embeddings as E
from monogram import embeddings_gemma as G


def test_gemma_prompt_is_asymmetric():
    assert G.gemma_prompt("deploy the model", query=True) == "task: search result | query: deploy the model"
    assert G.gemma_prompt("notes here", query=False) == "title: none | text: notes here"


def test_mean_pool_respects_mask():
    toks = [[1.0, 2.0], [3.0, 4.0], [100.0, 100.0]]  # 3rd token padding
    assert G.mean_pool(toks, [1, 1, 0]) == [2.0, 3.0]  # mean of first two only


def test_mean_pool_all_masked_no_div_zero():
    assert G.mean_pool([[1.0, 1.0]], [0]) == [0.0, 0.0]


def test_mean_pool_empty():
    assert G.mean_pool([], []) == []


def test_default_model_is_gemma_and_routes_local():
    assert E.DEFAULT_LOCAL_MODEL.startswith("google/embeddinggemma")
    assert E._is_gemma("google/embeddinggemma-300m")
    assert not E._is_gemma("BAAI/bge-m3")
    assert not E.is_api_model("google/embeddinggemma-300m")  # local, not litellm

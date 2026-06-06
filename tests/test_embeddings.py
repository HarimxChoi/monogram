"""Embedding quantization / encoding — pure, stdlib-only (no API)."""
import base64
import math

from monogram import embeddings as E


def _unit(v):
    return math.sqrt(sum(x * x for x in v))


def test_truncate_renormalize_length_and_norm():
    v = [0.1 * i for i in range(1000)]
    out = E.truncate_renormalize(v, dims=4)
    assert len(out) == 4
    assert abs(_unit(out) - 1.0) < 1e-9


def test_truncate_renormalize_pads_short_input():
    out = E.truncate_renormalize([3.0, 4.0], dims=4)
    assert len(out) == 4
    assert out[2] == 0.0 and out[3] == 0.0
    assert abs(_unit(out) - 1.0) < 1e-9   # [0.6, 0.8, 0, 0]


def test_quantize_clamps_to_signed_127():
    q = E.quantize_int8([1.5, -2.0, 0.0, 0.5])
    assert q == [127, -127, 0, 64]
    assert min(q) >= -127 and max(q) <= 127


def test_encode_decode_roundtrip():
    q = E.quantize_int8(E.truncate_renormalize([0.2 * i for i in range(8)], dims=8))
    assert E.decode_vec(E.encode_vec(q)) == q


def test_l2_normalize_unit_and_zero():
    v = E.l2_normalize([3.0, 4.0])
    assert abs(_unit(v) - 1.0) < 1e-9
    assert E.l2_normalize([0.0, 0.0]) == [0.0, 0.0]   # zero vector is a no-op


def test_truncate_renormalize_default_is_mrl_target():
    out = E.truncate_renormalize([0.01] * 3072)       # default = MRL_TARGET_DIM
    assert len(out) == E.MRL_TARGET_DIM == 768


def test_prepare_for_storage_is_dimension_agnostic():
    # prepare_for_storage no longer truncates — it stores whatever dim it's given,
    # so non-Gemini models (1024, 384, ...) round-trip at their native dimension.
    for dim in (384, 768, 1024):
        vec = E.l2_normalize([0.1 * i for i in range(1, dim + 1)])
        b64 = E.prepare_for_storage(vec)
        assert len(base64.b64decode(b64)) == dim
        assert len(E.decode_vec(b64)) == dim


def test_dot_int8_self_exceeds_cross():
    a = E.quantize_int8(E.truncate_renormalize([1.0, 0.0, 0.0, 0.0], dims=4))
    b = E.quantize_int8(E.truncate_renormalize([0.0, 1.0, 0.0, 0.0], dims=4))
    assert E.dot_int8(a, a) > E.dot_int8(a, b)


def test_dot_int8_monotonic_with_similarity():
    base = E.quantize_int8(E.truncate_renormalize([1.0, 0.0, 0.0, 0.0], dims=4))
    near = E.quantize_int8(E.truncate_renormalize([0.9, 0.1, 0.0, 0.0], dims=4))
    far = E.quantize_int8(E.truncate_renormalize([0.1, 0.9, 0.0, 0.0], dims=4))
    assert E.dot_int8(base, near) > E.dot_int8(base, far)


# ---- backend routing + API provider mapping (pure) --------------------------

def test_is_api_model_routing():
    assert E.is_api_model("gemini/gemini-embedding-001")
    assert E.is_api_model("openai/text-embedding-3-small")
    assert E.is_api_model("voyage/voyage-3.5")
    assert E.is_api_model("ollama/nomic-embed-text")
    assert not E.is_api_model("BAAI/bge-m3")                 # bare HF id → local fastembed
    assert not E.is_api_model("google/embeddinggemma-300m")  # → local gemma backend


def test_is_mrl_only_for_known_matryoshka():
    assert E.is_mrl("gemini/gemini-embedding-001")
    assert E.is_mrl("openai/text-embedding-3-small")
    assert not E.is_mrl("voyage/voyage-3.5")    # MRL but different param → treat native
    assert not E.is_mrl("BAAI/bge-m3")


def test_retrieval_params_per_provider():
    assert E.retrieval_params("gemini/gemini-embedding-001", query=True) == {"task_type": "RETRIEVAL_QUERY"}
    assert E.retrieval_params("gemini/gemini-embedding-001", query=False) == {"task_type": "RETRIEVAL_DOCUMENT"}
    assert E.retrieval_params("voyage/voyage-3.5", query=True) == {"input_type": "query"}
    assert E.retrieval_params("cohere/embed-v4.0", query=False) == {"input_type": "search_document"}
    assert E.retrieval_params("openai/text-embedding-3-small", query=True) == {}   # symmetric


def test_target_dim():
    assert E.target_dim("gemini/gemini-embedding-001") == 768
    assert E.target_dim("openai/text-embedding-3-small", configured=256) == 256
    assert E.target_dim("voyage/voyage-3.5") is None      # not MRL → native dim
    assert E.target_dim("BAAI/bge-m3") is None

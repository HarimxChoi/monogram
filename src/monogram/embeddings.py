"""Text embeddings: onnxruntime (EmbeddingGemma), fastembed, or litellm by model prefix."""
from __future__ import annotations

import array
import base64
import logging
import math

log = logging.getLogger("monogram.embeddings")

DEFAULT_LOCAL_MODEL = "google/embeddinggemma-300m"
MRL_TARGET_DIM = 768   # truncation only applied to MRL-capable API models (gemini/openai-3)
_SCALE = 127           # symmetric int8: clamp to [-127, 127], not [-128, 127]
_BATCH = 64

# Bare HF ids not in this list route to local fastembed.
_API_PREFIXES = (
    "gemini", "openai", "anthropic", "voyage", "cohere",
    "ollama", "azure", "mistral", "github", "vertex_ai", "bedrock",
)

_MRL_MODELS = (
    "gemini/gemini-embedding-001",
    "openai/text-embedding-3-small",
    "openai/text-embedding-3-large",
)

# bge-m3/gte are symmetric so the default needs no prefix; only these need query_embed.
_LOCAL_QUERY_PREFIX = ("intfloat/multilingual-e5", "nomic-ai/nomic", "google/embeddinggemma")


def resolve_model() -> str:
    try:
        from .vault_config import load_vault_config
        configured = (load_vault_config().embedding_model or "").strip()
    except Exception:
        configured = ""
    return configured or DEFAULT_LOCAL_MODEL


def is_api_model(model: str) -> bool:
    return model.split("/", 1)[0] in _API_PREFIXES


def is_mrl(model: str) -> bool:
    return any(model.startswith(m) for m in _MRL_MODELS)


def retrieval_params(model: str, *, query: bool) -> dict:
    # Gemini uses task_type, Voyage/Cohere use input_type — do NOT use drop_params to gate this.
    prefix = model.split("/", 1)[0]
    if prefix == "gemini":
        return {"task_type": "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"}
    if prefix == "voyage":
        return {"input_type": "query" if query else "document"}
    if prefix == "cohere":
        return {"input_type": "search_query" if query else "search_document"}
    return {}  # openai is symmetric; ollama/others → none


def target_dim(model: str, configured: int = 0) -> int | None:
    if not is_mrl(model):
        return None
    return configured if configured and configured > 0 else MRL_TARGET_DIM


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return list(vec) if norm == 0.0 else [x / norm for x in vec]


def truncate_renormalize(vec: list[float], dims: int = MRL_TARGET_DIM) -> list[float]:
    # A Matryoshka sub-slice is not unit-norm — must re-normalize after truncating.
    head = list(vec[:dims])
    if len(head) < dims:
        head.extend([0.0] * (dims - len(head)))
    return l2_normalize(head)


def quantize_int8(vec: list[float]) -> list[int]:
    out: list[int] = []
    for x in vec:
        q = int(round(x * _SCALE))
        out.append(127 if q > 127 else -127 if q < -127 else q)
    return out


def encode_vec(int8_vec: list[int]) -> str:
    return base64.b64encode(array.array("b", int8_vec).tobytes()).decode("ascii")


def decode_vec(b64: str) -> list[int]:
    a = array.array("b")
    a.frombytes(base64.b64decode(b64))
    return a.tolist()


def prepare_for_storage(unit_vec: list[float]) -> str:
    return encode_vec(quantize_int8(unit_vec))


def dot_int8(a: list[int], b: list[int]) -> int:
    return sum(x * y for x, y in zip(a, b))


async def embed_documents(texts: list[str]) -> list[list[float]]:
    return await _embed(texts, query=False)


async def embed_query(text: str) -> list[float]:
    vecs = await _embed([text], query=True)
    return vecs[0] if vecs else []


async def _embed(texts: list[str], *, query: bool) -> list[list[float]]:
    if not texts:
        return []
    model = resolve_model()
    if is_api_model(model):
        return await _embed_api(texts, model, query=query)
    return _embed_local(texts, model, query=query)


# fastembed ONNX sessions are expensive to construct; cache one per model id.
_LOCAL_CACHE: dict = {}


def _local_model(model: str):
    inst = _LOCAL_CACHE.get(model)
    if inst is None:
        from fastembed import TextEmbedding
        inst = TextEmbedding(model_name=model)
        _LOCAL_CACHE[model] = inst
    return inst


def _is_gemma(model: str) -> bool:
    return model.startswith("google/embeddinggemma")


def _local_mrl_dim() -> int:
    try:
        from .vault_config import load_vault_config
        d = int(load_vault_config().embedding_dimensions or 0)
        if d > 0:
            return d
    except Exception:
        pass
    from .embeddings_gemma import MRL_DIM
    return MRL_DIM


def _embed_local(texts: list[str], model: str, *, query: bool) -> list[list[float]]:
    if _is_gemma(model):
        from . import embeddings_gemma
        return embeddings_gemma.embed(texts, query=query, mrl_dim=_local_mrl_dim())
    emb = _local_model(model)
    use_query = query and any(model.startswith(p) for p in _LOCAL_QUERY_PREFIX)
    gen = emb.query_embed(texts) if use_query else emb.embed(texts, batch_size=_BATCH)
    return [l2_normalize([float(x) for x in v]) for v in gen]


async def _embed_api(texts: list[str], model: str, *, query: bool) -> list[list[float]]:
    import litellm

    from .models import embedding_credentials

    api_key, api_base = embedding_credentials(model)
    configured = 0
    try:
        from .vault_config import load_vault_config
        configured = int(load_vault_config().embedding_dimensions or 0)
    except Exception:
        configured = 0
    tdim = target_dim(model, configured)

    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        kwargs: dict = {"model": model, "input": texts[i : i + _BATCH], "drop_params": True}
        kwargs.update(retrieval_params(model, query=query))
        if tdim:
            kwargs["dimensions"] = tdim
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
        response = await litellm.aembedding(**kwargs)
        norm = (lambda v: truncate_renormalize(v, tdim)) if tdim else l2_normalize
        out.extend(norm(list(item["embedding"]) if isinstance(item, dict) else list(item.embedding))
                   for item in (getattr(response, "data", None) or response.get("data", [])))
    return out

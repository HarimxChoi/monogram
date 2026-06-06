"""EmbeddingGemma-300m via onnxruntime — fastembed doesn't ship it; weights not committed (>100MB)."""
from __future__ import annotations

import logging

from .embeddings import truncate_renormalize

log = logging.getLogger("monogram.embeddings.gemma")

MODEL_ID = "google/embeddinggemma-300m"          # logical id used in config
ONNX_REPO = "onnx-community/embeddinggemma-300m-ONNX"
ONNX_FILE = "onnx/model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"
NATIVE_DIM = 768
MRL_DIM = 256  # default truncation tradeoff: small index at ~-1.5 MMTEB
MAX_TOKENS = 2048
BATCH = 32


def gemma_prompt(text: str, *, query: bool) -> str:
    return f"task: search result | query: {text}" if query else f"title: none | text: {text}"


def mean_pool(token_embeddings: list[list[float]], attention_mask: list[int]) -> list[float]:
    # Pure list-based so it's testable without numpy; caller converts ONNX array via .tolist().
    if not token_embeddings:
        return []
    dim = len(token_embeddings[0])
    summed = [0.0] * dim
    count = 0
    for emb, m in zip(token_embeddings, attention_mask):
        if not m:
            continue
        count += 1
        for j in range(dim):
            summed[j] += emb[j]
    count = count or 1
    return [s / count for s in summed]


class _GemmaEmbedder:

    def __init__(self, mrl_dim: int = MRL_DIM):
        self.mrl_dim = mrl_dim
        self._sess = None
        self._tok = None
        self._input_names: set[str] = set()
        self._out_name = "sentence_embedding"

    def _load(self) -> None:
        if self._sess is not None:
            return
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        model_path = hf_hub_download(ONNX_REPO, ONNX_FILE)
        # int8 weights may live in an external-data sibling that onnxruntime needs co-located.
        try:
            hf_hub_download(ONNX_REPO, ONNX_FILE + "_data")
        except Exception as e:  # some exports inline the weights
            log.debug("gemma: no external data file (%s)", e)
        tok_path = hf_hub_download(ONNX_REPO, TOKENIZER_FILE)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 0  # 0 = auto-detect cores
        self._sess = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._sess.get_inputs()}
        out_names = {o.name for o in self._sess.get_outputs()}
        # Prefer sentence_embedding (already pooled+normalized via Dense head); fallback pools token states.
        self._out_name = "sentence_embedding" if "sentence_embedding" in out_names \
            else next(iter(out_names))

        self._tok = Tokenizer.from_file(tok_path)
        self._tok.enable_truncation(max_length=MAX_TOKENS)
        self._tok.enable_padding()  # pads to longest in batch; attention_mask excludes pads

    def embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        import numpy as np

        prompts = [gemma_prompt(t, query=query) for t in texts]
        out: list[list[float]] = []
        for i in range(0, len(prompts), BATCH):
            encs = self._tok.encode_batch(prompts[i : i + BATCH])
            ids = np.asarray([e.ids for e in encs], dtype=np.int64)
            mask = np.asarray([e.attention_mask for e in encs], dtype=np.int64)
            candidates = {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": np.zeros_like(ids),
                "position_ids": np.tile(np.arange(ids.shape[1]), (ids.shape[0], 1)),
            }
            feeds = {k: v for k, v in candidates.items() if k in self._input_names}
            res = np.asarray(self._sess.run([self._out_name], feeds)[0], dtype=np.float32)
            for r in range(res.shape[0]):
                out.append(self._postprocess(res[r], encs[r].attention_mask))
        return out

    def _postprocess(self, row, mask: list[int]) -> list[float]:
        # sentence_embedding is (dim,) already pooled; token-level (seq, dim) is mean-pooled as fallback.
        vec = mean_pool(row.tolist(), mask) if getattr(row, "ndim", 1) == 2 else list(row)
        return truncate_renormalize(vec, self.mrl_dim)


_EMBEDDER: _GemmaEmbedder | None = None


def get_embedder(mrl_dim: int = MRL_DIM) -> _GemmaEmbedder:
    global _EMBEDDER
    if _EMBEDDER is None or _EMBEDDER.mrl_dim != mrl_dim:
        _EMBEDDER = _GemmaEmbedder(mrl_dim)
    return _EMBEDDER


def embed(texts: list[str], *, query: bool, mrl_dim: int = MRL_DIM) -> list[list[float]]:
    return get_embedder(mrl_dim).embed(texts, query=query)

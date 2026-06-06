"""Cross-encoder reranker via onnxruntime; falls back to retriever order if unavailable."""
from __future__ import annotations

import logging

log = logging.getLogger("monogram.reranker")

MODEL_REPO = "Xenova/ms-marco-MiniLM-L-6-v2"
ONNX_FILE = "onnx/model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"
MAX_TOKENS = 512
BATCH = 16


class _Reranker:
    def __init__(self):
        self._sess = None
        self._tok = None
        self._input_names: set[str] = set()

    def _load(self) -> None:
        if self._sess is not None:
            return
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        model_path = hf_hub_download(MODEL_REPO, ONNX_FILE)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 0
        self._sess = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._sess.get_inputs()}
        self._tok = Tokenizer.from_file(hf_hub_download(MODEL_REPO, TOKENIZER_FILE))
        self._tok.enable_truncation(max_length=MAX_TOKENS)
        self._tok.enable_padding()

    def score(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        self._load()
        import numpy as np

        out: list[float] = []
        for i in range(0, len(docs), BATCH):
            encs = self._tok.encode_batch([(query, d) for d in docs[i : i + BATCH]])
            feeds = {
                "input_ids": np.asarray([e.ids for e in encs], dtype=np.int64),
                "attention_mask": np.asarray([e.attention_mask for e in encs], dtype=np.int64),
                "token_type_ids": np.asarray([e.type_ids for e in encs], dtype=np.int64),
            }
            feeds = {k: v for k, v in feeds.items() if k in self._input_names}
            logits = np.asarray(self._sess.run(None, feeds)[0], dtype=np.float32).reshape(-1)
            out.extend(float(x) for x in logits)
        return out


_RERANKER: _Reranker | None = None


def _get() -> _Reranker:
    global _RERANKER
    if _RERANKER is None:
        _RERANKER = _Reranker()
    return _RERANKER


def rerank_records(query: str, records: list[dict], k: int, *, text_key: str = "text") -> list[dict]:
    # Returns [] on any failure so callers fall back gracefully to retriever order.
    if not records:
        return []
    try:
        scores = _get().score(query, [r.get(text_key, "") or r.get("heading", "") for r in records])
    except Exception as e:
        log.warning("reranker unavailable (%s) — falling back to retriever order", e)
        return []
    order = sorted(range(len(records)), key=lambda i: -scores[i])
    out: list[dict] = []
    for i in order[:k]:
        rec = dict(records[i])
        rec["_rerank"] = scores[i]
        out.append(rec)
    return out

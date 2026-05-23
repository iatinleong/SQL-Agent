"""Phase 3：BGE-M3 向量搜尋，從 case_summaries/ 找出 Top-K 最相似案例。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .config import BASE_DIR, BGE_MODEL_PATH
from .summarizer import SUMMARIES_DIR, load_summaries

_EMBEDDINGS_PATH: Path = BASE_DIR / "all_cases_embeddings.npz"

_model = None
_index: Optional[dict[str, np.ndarray]] = None  # case_id → normalized 1024-dim vec


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(BGE_MODEL_PATH, device="cpu")
    return _model


def _get_index() -> dict[str, np.ndarray]:
    """載入或建立 case_summaries/ 的 embedding index，cache 存於 all_cases_embeddings.npz。"""
    global _index
    if _index is not None:
        return _index

    summaries = load_summaries()
    if not summaries:
        raise RuntimeError("找不到任何摘要，請先執行：python -m agent --summarize")

    # cache 有效條件：npz 存在，且 ids 集合與 case_summaries/ 完全一致
    if _EMBEDDINGS_PATH.exists():
        data = np.load(_EMBEDDINGS_PATH, allow_pickle=False)
        if set(data["ids"].tolist()) == set(summaries.keys()):
            ids: list[str] = data["ids"].tolist()
            vecs: np.ndarray = data["vecs"]
            _index = {cid: vecs[i] for i, cid in enumerate(ids)}
            print(f"  [Retriever] 載入 cache：{len(_index)} 筆")
            return _index

    print(f"  [Retriever] 建立 index（{len(summaries)} 筆）...")
    model = _get_model()
    ids = list(summaries.keys())
    vecs = model.encode(
        [summaries[cid] for cid in ids],
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    np.savez(_EMBEDDINGS_PATH, ids=np.array(ids), vecs=vecs)
    _index = {cid: vecs[i] for i, cid in enumerate(ids)}
    print(f"  [Retriever] 已存至 {_EMBEDDINGS_PATH.name}")
    return _index


@dataclass
class RetrievalHit:
    case_id: str
    score: float
    rank: int


def retrieve(query: str, all_cases: list[dict], top_k: int = 5) -> list[RetrievalHit]:
    """用 query 對 case_summaries/ 做 cosine 搜尋，回傳 Top-K。"""
    index = _get_index()
    query_vec: np.ndarray = _get_model().encode([query], normalize_embeddings=True)[0]

    scored = [
        (float(np.dot(query_vec, vec)), cid)
        for cid, vec in index.items()
    ]
    scored.sort(reverse=True)

    return [
        RetrievalHit(case_id=cid, score=score, rank=rank)
        for rank, (score, cid) in enumerate(scored[:top_k], start=1)
    ]

"""共用 BGE-M3 embedding model。

Thread-safety 設計：
  - _init_lock：double-checked locking，確保模型只初始化一次（修 race condition）
  - _encode_lock：序列化所有 encode() 呼叫，避免多 session 同時搶 CPU
    → 這就是 queue 效果：第二個 session 的 encode 會排隊等第一個完成
"""
from __future__ import annotations

import threading

import numpy as np

from .config import BGE_MODEL_PATH

_model = None
_init_lock = threading.Lock()
_encode_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _init_lock:
            if _model is None:  # double-checked locking
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(BGE_MODEL_PATH, device="cpu")
    return _model


def encode(texts: list[str], **kwargs) -> np.ndarray:
    """Thread-safe encode，同一時間只允許一個 encode 任務執行。"""
    with _encode_lock:
        return _get_model().encode(texts, **kwargs)

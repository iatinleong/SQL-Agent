"""共用 BGE-M3 embedding model。

Thread-safety 設計：
  - _init_lock：double-checked locking，確保模型只初始化一次（修 race condition）
  - _encode_lock：序列化所有 encode() 呼叫，避免多 session 同時搶 CPU
    → 這就是 queue 效果：第二個 session 的 encode 會排隊等第一個完成
  - _waiting：記錄正在等待 _encode_lock 的 thread 數，供 UI 顯示排隊人數
"""
from __future__ import annotations

import threading

import numpy as np

from .config import BGE_MODEL_PATH

_model = None
_init_lock = threading.Lock()
_encode_lock = threading.Lock()
_waiting = 0  # 等待取得 _encode_lock 的 thread 數（不含正在執行的）

_local = threading.local()  # per-thread callback storage


def _get_model():
    global _model
    if _model is None:
        with _init_lock:
            if _model is None:  # double-checked locking
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(BGE_MODEL_PATH, device="cpu")
    return _model


def set_waiting_callback(callback) -> None:
    """註冊當前 thread 的排隊通知 callback：callback(n_others_waiting)。
    在 encode() 確認需要等待時，從同一個 thread 呼叫，讓 Streamlit UI 可以即時更新。
    """
    _local.callback = callback


def clear_waiting_callback() -> None:
    _local.callback = None


def encode(texts: list[str], **kwargs) -> np.ndarray:
    """Thread-safe encode，同一時間只允許一個 encode 任務執行。"""
    global _waiting
    _waiting += 1

    # 在 acquire() 之前，若已有人佔用或有人排隊，透過 callback 通知 UI（仍在同一 thread）
    cb = getattr(_local, 'callback', None)
    if cb:
        n_others = max(0, _waiting - 1)   # 其他正在排隊的 session 數
        is_busy = _encode_lock.locked()    # 是否有 session 正在執行
        if is_busy or n_others > 0:
            try:
                cb(n_others)
            except Exception:
                pass

    _acquired = False
    try:
        with _encode_lock:
            _waiting -= 1
            _acquired = True
            return _get_model().encode(texts, **kwargs)
    finally:
        if not _acquired:
            _waiting -= 1

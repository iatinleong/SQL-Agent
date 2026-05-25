"""實驗記錄器：攔截 stdout，實驗結束時存成 .txt + .json。"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime
from io import StringIO
from pathlib import Path

EXPERIMENT_DIR: Path = Path(__file__).parent.parent / "experiment"


class _Tee:
    """同時寫入原始 stdout 和 buffer。"""

    def __init__(self, original: object, buffer: StringIO) -> None:
        self._orig = original
        self._buf = buffer

    def write(self, data: str) -> int:
        self._orig.write(data)  # type: ignore[attr-defined]
        self._buf.write(data)
        return len(data)

    def flush(self) -> None:
        self._orig.flush()  # type: ignore[attr-defined]
        self._buf.flush()

    def reconfigure(self, **kwargs: object) -> None:
        pass


@contextmanager
def log_experiment(name: str):
    """
    用法：
        with log_experiment("phase1_p3") as log:
            ...
            log["results"] = my_results

    結束時自動存：
        experiment/<timestamp>_<name>.txt   完整 terminal 輸出
        experiment/<timestamp>_<name>.json  結構化結果 + log 全文
    """
    EXPERIMENT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{ts}_{name}"

    buf = StringIO()
    orig_stdout = sys.stdout
    sys.stdout = _Tee(orig_stdout, buf)  # type: ignore[assignment]

    meta: dict = {"name": name, "timestamp": ts, "results": None}
    try:
        yield meta
    finally:
        sys.stdout = orig_stdout
        log_text = buf.getvalue()

        # ── 本機檔案 ──────────────────────────────────────────────
        EXPERIMENT_DIR.mkdir(exist_ok=True)
        txt_path = EXPERIMENT_DIR / f"{stem}.txt"
        txt_path.write_text(log_text, encoding="utf-8")

        payload = {
            "name": meta["name"],
            "timestamp": meta["timestamp"],
            "results": meta.get("results"),
            "log": log_text,
        }
        json_path = EXPERIMENT_DIR / f"{stem}.json"
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ── Supabase ──────────────────────────────────────────────
        from . import supabase_logger
        ok = supabase_logger.insert("experiments", payload)
        tag = "✓ Supabase + 本機" if ok else "本機"
        print(f"\n[實驗記錄] → {tag}  experiment/{stem}.json")

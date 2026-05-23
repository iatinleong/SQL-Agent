"""Pydantic schema：Phase 1 LLM structured output 的回傳型別。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SceneConfidence(BaseModel):
    標籤: str
    分數: float


class ClassificationResult(BaseModel):
    主要場景: str
    次要場景: Optional[str] = None
    各標籤置信度: list[SceneConfidence]
    分類理由: str

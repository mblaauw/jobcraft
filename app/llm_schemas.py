"""Pydantic structured-output contracts."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator


class Verdict(BaseModel):
    score: int = Field(ge=0, le=100)
    why: str
    concerns: list[str] = Field(default_factory=list, max_length=8)
    questions_for_employer: list[str] = Field(default_factory=list, max_length=6)


class CvTailoring(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = Field(min_length=120, max_length=800)
    strong_points: list[str] = Field(min_length=5, max_length=5)

    @field_validator("strong_points")
    @classmethod
    def point_length(cls, values: list[str]) -> list[str]:
        if any(len(item) > 70 for item in values):
            raise ValueError("strong points must be at most 70 characters")
        return values


@lru_cache(maxsize=6)
def cv_tailoring_model(strong_count: int = 5) -> type[CvTailoring]:
    if isinstance(strong_count, bool) or not 3 <= strong_count <= 8:
        raise ValueError("strong point count must be 3–8")
    point = Annotated[str, Field(min_length=1, max_length=70)]
    return create_model(
        f"CvTailoring{strong_count}",
        __base__=CvTailoring,
        strong_points=(list[point], Field(min_length=strong_count, max_length=strong_count)),
    )


class Letter(BaseModel):
    paragraphs: list[str] = Field(min_length=1)

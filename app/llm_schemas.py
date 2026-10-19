"""Pydantic structured-output contracts."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Verdict(BaseModel):
    score: int = Field(ge=0, le=100)
    why: str
    concerns: list[str] = Field(default_factory=list, max_length=8)
    questions_for_employer: list[str] = Field(default_factory=list, max_length=6)


class CvTailoring(BaseModel):
    profile: str = Field(min_length=120, max_length=800)
    strong_points: list[str] = Field(min_length=5, max_length=5)

    @field_validator("strong_points")
    @classmethod
    def point_length(cls, values: list[str]) -> list[str]:
        if any(len(item) > 70 for item in values):
            raise ValueError("strong points must be at most 70 characters")
        return values


class Letter(BaseModel):
    paragraphs: list[str] = Field(min_length=1)

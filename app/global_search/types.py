"""Hit / group shapes for Global Search grouped cards."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    entity_type: str
    entity_id: str
    entity_name: str
    matched_field: str = ""
    matched_value: str = ""
    description: Optional[str] = None
    matchSource: Optional[str] = None
    ifileName: Optional[str] = None
    name: Optional[str] = None
    modifiedDateandtime: Optional[str] = None
    dateandtime: Optional[str] = None
    requestNo: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    id: Optional[dict[str, Any]] = None
    navigation_url: Optional[str] = None


class SearchGroup(BaseModel):
    entity_type: str
    label: str
    count: int
    hits: list[SearchHit]


class GlobalSearchResult(BaseModel):
    query: str
    groups: list[SearchGroup] = Field(default_factory=list)

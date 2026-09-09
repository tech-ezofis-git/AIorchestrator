"""Hit shapes for Global Search flat results."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    """One flat hit. `type` is the frontend discriminator (document|repository|workflow|form)."""

    type: str = ""
    entity_type: str = ""
    entity_id: str = ""
    entity_name: str = ""
    matched_field: str = ""
    matched_value: str = ""
    description: str = ""
    matchSource: Optional[str] = None
    ifileName: Optional[str] = None
    name: Optional[str] = None
    modifiedDateandtime: str = ""
    dateandtime: str = ""
    requestNo: Optional[str] = None
    formKind: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    id: Optional[dict[str, Any]] = None
    navigation_url: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        if not self.type and self.entity_type:
            self.type = self.entity_type
        if not self.entity_type and self.type:
            self.entity_type = self.type


class GlobalSearchResult(BaseModel):
    query: str
    hits: list[SearchHit] = Field(default_factory=list)

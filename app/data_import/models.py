"""JSON body for POST /api/ezDataImport — same shape as ezofis function_app ezDataImport."""
from __future__ import annotations

from typing import List, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DataImportRequest(BaseModel):
    fileName: str
    id: Union[str, int]
    formId: str
    tenantId: str
    notifyId: Union[str, int]
    filepath: str
    conditionColumn: List[str] = Field(default_factory=list)
    userid: str

    @field_validator("tenantId", mode="before")
    @classmethod
    def validate_tenant_id_is_uuid(cls, value):
        if value is None or str(value).strip() == "":
            raise ValueError("tenantId is required")
        return str(UUID(str(value).strip()))

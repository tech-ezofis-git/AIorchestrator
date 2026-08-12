"""The contract every future tool/agent (Search, Summary, Insight, Forecast,
OCR, Mail, AP, ...) must implement so the Dispatcher can call it uniformly.

Nothing in Phase 1 wires this up yet — Chat calls the LLM directly with no
tools — but the schema is defined now so later phases can register tools
without changing the calling convention.
"""
from pydantic import BaseModel, Field


class ToolSchema(BaseModel):
    """Describes a single callable tool in JSON-schema-compatible form.

    Mirrors the shape used by most LLM function/tool-calling APIs so instances
    of this model can be passed straight through to the LLM adapter later.
    """

    name: str = Field(..., description="Unique tool identifier, e.g. 'search_documents'.")
    description: str = Field(..., description="What the tool does — shown to the LLM.")
    parameters: dict = Field(
        ..., description="JSON schema (type: object, properties, required, ...) for the tool's input."
    )
    requires_confirmation: bool = Field(
        default=False,
        description=(
            "True for side-effect / irreversible actions (e.g. Mail send, AP approval) "
            "that must be confirmed by a human or an explicit policy before executing."
        ),
    )

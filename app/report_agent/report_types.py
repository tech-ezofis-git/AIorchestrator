"""Data-driven Report Type registry — extend here without rewriting the agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models.report_agent import ReportTypeInfo


@dataclass(frozen=True)
class ReportTypeDefinition:
    key: str
    name: str
    description: str
    scope: str  # workflows | repositories
    required_params: tuple[str, ...] = ()
    example_description: str = ""
    schema_focus: tuple[str, ...] = ()

    def to_model(self) -> ReportTypeInfo:
        return ReportTypeInfo(
            key=self.key,
            name=self.name,
            description=self.description,
            scope=self.scope,
            required_params=list(self.required_params),
            example_description=self.example_description or None,
        )


REPORT_TYPES: dict[str, ReportTypeDefinition] = {
    "all_workflows": ReportTypeDefinition(
        key="all_workflows",
        name="All Workflows",
        description="Report across all workflows based on the user description.",
        scope="workflows",
        example_description="Awaiting for your action",
        schema_focus=("workflow", "wworkflow", "inbox_", "workflow_instances_", "workflow_tasks_"),
    ),
    "specific_workflow": ReportTypeDefinition(
        key="specific_workflow",
        name="Specified Workflow",
        description="Report for one workflow. Requires workflow name.",
        scope="workflows",
        required_params=("workflowName",),
        example_description="Total Approved with Score",
        schema_focus=("workflow", "wworkflow", "inbox_", "workflow_instances_", "workflow_tasks_", "ezfb_"),
    ),
    "all_repositories": ReportTypeDefinition(
        key="all_repositories",
        name="All Repositories",
        description="Report across all repositories based on the user description.",
        scope="repositories",
        example_description="Total Archived Documents",
        schema_focus=("repository", "wrepository", "items_"),
    ),
    "specific_repository": ReportTypeDefinition(
        key="specific_repository",
        name="Specified Repository",
        description="Report for one repository. Requires repository name.",
        scope="repositories",
        required_params=("repositoryName",),
        example_description="Total Signed Documents",
        schema_focus=("repository", "wrepository", "items_"),
    ),
}


def list_report_types() -> list[ReportTypeInfo]:
    return [t.to_model() for t in REPORT_TYPES.values()]


def get_report_type(key: Optional[str]) -> Optional[ReportTypeDefinition]:
    if not key:
        return None
    return REPORT_TYPES.get(str(key).strip().lower())


def validate_report_type_input(
    *,
    report_type: str,
    workflow_name: Optional[str] = None,
    repository_name: Optional[str] = None,
    description: Optional[str] = None,
    require_description: bool = True,
) -> ReportTypeDefinition:
    rt = get_report_type(report_type)
    if rt is None:
        raise ValueError(
            f"Unsupported report type '{report_type}'. "
            f"Use: {', '.join(sorted(REPORT_TYPES))}."
        )
    if require_description and not (description or "").strip():
        raise ValueError("description is required.")
    if "workflowName" in rt.required_params and not (workflow_name or "").strip():
        raise ValueError("workflowName is required for specific_workflow.")
    if "repositoryName" in rt.required_params and not (repository_name or "").strip():
        raise ValueError("repositoryName is required for specific_repository.")
    return rt

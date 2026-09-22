"""Report Agent package — dynamic template prompt & report plan generation with live database data."""
from app.report_agent.data_service import execute_report_query, sample_table_values
from app.report_agent.planner import create_report_plan
from app.report_agent.report_validator import validate_report_data
from app.report_agent.service import ReportAgentService
from app.report_agent.sql_generator import generate_sql
from app.report_agent.sql_validator import validate_read_only_sql
from app.report_agent.templates import SUPPORTED_TEMPLATES, get_template, list_templates

__all__ = [
    "ReportAgentService",
    "SUPPORTED_TEMPLATES",
    "list_templates",
    "get_template",
    "create_report_plan",
    "generate_sql",
    "validate_read_only_sql",
    "execute_report_query",
    "sample_table_values",
    "validate_report_data",
]

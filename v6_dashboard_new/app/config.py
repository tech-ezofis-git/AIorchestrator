"""Settings from environment / local.settings.json."""
from __future__ import annotations

import os

from app.env_loader import load_local_settings

load_local_settings()


def _get_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


class Settings:
    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.azure_sql_server = _get_env("AZURE_SQL_SERVER", default="DESKTOP-J99I11O")
        self.azure_sql_user = _get_env("AZURE_SQL_USER", default="sa")
        self.azure_sql_password = _get_env("AZURE_SQL_PASSWORD")
        self.azure_sql_database_pattern = _get_env(
            "AZURE_SQL_DATABASE_PATTERN",
            default="ezofis_Tenant_{tenant_id}",
        )
        self.azure_sql_driver = _get_env(
            "AZURE_SQL_DRIVER",
            default="ODBC Driver 18 for SQL Server",
        )
        self.azure_sql_connection_timeout = int(
            _get_env("AZURE_SQL_CONNECTION_TIMEOUT", default="30")
        )
        self.azure_sql_trust_server_certificate = _get_env(
            "AZURE_SQL_TRUST_SERVER_CERTIFICATE",
            default="true",
        ).lower() in {"1", "true", "yes", "on"}
        self.azure_sql_catalog_database = _get_env(
            "AZURE_SQL_CATALOG_DATABASE",
            default="Ezofis_catalog_new",
        )
        self.azure_sql_default_tenant_id = _get_env(
            "AZURE_SQL_DEFAULT_TENANT_ID",
            default="0B3E1B77-4A6C-46F2-83EE-2F0A5B84956B",
        )
        self.azure_openai_endpoint = _get_env("AZURE_OPENAI_ENDPOINT")
        self.azure_openai_api_key = _get_env("AZURE_OPENAI_API_KEY")
        self.azure_openai_deployment = _get_env("AZURE_OPENAI_DEPLOYMENT", default="gpt-4o-mini")
        self.azure_openai_api_version = _get_env(
            "AZURE_OPENAI_API_VERSION",
            default="2024-08-01-preview",
        )
        self.azure_openai_fallback_deployment = _get_env("AZURE_OPENAI_FALLBACK_DEPLOYMENT")
        self.azure_openai_fallback_api_version = _get_env(
            "AZURE_OPENAI_FALLBACK_API_VERSION",
            default="2025-01-01-preview",
        )
        self.azure_openai_ssl_verify = _get_env(
            "AZURE_OPENAI_SSL_VERIFY",
            default="false",
        ).lower() in {"1", "true", "yes", "on"}


settings = Settings()

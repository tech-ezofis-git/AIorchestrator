"""POST /api/ezDataImport — validation and wiring. No live catalog/blob/secrets."""
from uuid import UUID

import pytest

from app.data_import.ident import resolve_import_table_name
from app.data_import.models import DataImportRequest
from app.data_import.xlsx_import import control_db_column

_VALID_BODY = {
    "fileName": "po.xlsx",
    "id": 123,
    "formId": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "tenantId": "11111111-1111-1111-1111-111111111111",
    "notifyId": 456,
    "filepath": "folder/file.xlsx",
    "conditionColumn": ["itemid"],
    "userid": "22222222-2222-2222-2222-222222222222",
}


def test_resolve_import_table_name_from_form_guid():
    form_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    assert resolve_import_table_name(form_id) == "ezfb_aaaaaaaa_items"
    compact = form_id.replace("-", "")
    assert compact[:8] == "aaaaaaaa"


def test_resolve_import_table_name_from_numeric_token():
    assert resolve_import_table_name("98") == "ezfb_98_items"


def test_resolve_import_table_name_rejects_invalid():
    with pytest.raises(ValueError):
        resolve_import_table_name("bad form!")


def test_control_db_column_prefers_column_name():
    assert control_db_column("vendor_code", "pLIax1zKPXRCdlnCOLHzy") == "vendor_code"
    assert control_db_column("  ", "pLIax1zKPXRCdlnCOLHzy") == "pLIax1zKPXRCdlnCOLHzy"
    assert control_db_column(None, "jsonid1") == "jsonid1"


def test_choose_master_sheet_prefers_db_mapped_headers():
    import pandas as pd

    from app.data_import.xlsx_import import _choose_master_sheet

    frames = [
        ("Details", pd.DataFrame(columns=["Line", "Part Number", "Req Date"])),
        ("Master", pd.DataFrame(columns=["PO Number", "Supplier", "PO Date"])),
    ]
    normalized_mapping = {
        "ponumber": "PO_Number",
        "supplier": "Supplier",
        "podate": "PO_Date",
        "line": "2z2Rh5MpXEaiHSaWlMThr",
    }
    table_columns = ["PO_Number", "Supplier", "PO_Date", "PO_Line_Item"]
    assert _choose_master_sheet(frames, normalized_mapping, table_columns) == 1


def test_upsert_set_clause_does_not_qualify_target_columns():
    """Postgres rejects SET t.col = ...; targets must be unqualified."""
    import inspect

    from app.data_import import xlsx_import

    src = inspect.getsource(xlsx_import.execute_postgres_upsert_import)
    assert 'f"t.{pg_ident(col)} = s.{pg_ident(col)}"' not in src
    assert 'f"{pg_ident(col)} = s.{pg_ident(col)}"' in src
    assert "modified_at = '" in src
    assert "t.modified_at" not in src


def test_tenant_id_must_be_uuid(client):
    body = {**_VALID_BODY, "tenantId": "not-a-uuid"}
    response = client.post("/api/ezDataImport", json=body)
    assert response.status_code == 422
    assert "AccountKey" not in response.text
    assert "password" not in response.text.lower()


def test_non_xlsx_returns_400(client):
    body = {**_VALID_BODY, "fileName": "po.csv"}
    response = client.post("/api/ezDataImport", json=body)
    assert response.status_code == 400
    assert "xlsx" in response.json()["detail"].lower()


def test_catalog_sqlalchemy_url_adds_azure_ssl(monkeypatch):
    monkeypatch.setenv(
        "CATALOG_DATABASE_URL",
        "postgresql://u:p@ezv6psql.postgres.database.azure.com:5432/ezofis_catalog_new",
    )
    from app.config import get_settings
    from app.data_import.catalog import catalog_sqlalchemy_url, ensure_azure_ssl

    get_settings.cache_clear()
    url = catalog_sqlalchemy_url()
    assert url.startswith("postgresql+psycopg2://")
    assert "sslmode=require" in url
    assert "ezofis_catalog_new" in url
    already = "postgresql+psycopg2://u:p@host/db?sslmode=prefer"
    assert ensure_azure_ssl(already) == already
    get_settings.cache_clear()


def test_tenant_engine_url_uses_ezofis_tenant_prefix(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:p@ezv6psql.postgres.database.azure.com:5432/orchestrator",
    )
    monkeypatch.delenv("CATALOG_DATABASE_URL", raising=False)
    from app.config import get_settings
    from app.data_import.catalog import tenant_engine_url_from_app_settings

    get_settings.cache_clear()
    url = tenant_engine_url_from_app_settings("496a0db6-5267-47b7-972d-249023817dba")
    assert url.startswith("postgresql+psycopg2://")
    assert "ezofis_Tenant_496a0db6" in url
    assert "/orchestrator" not in url.split("?")[0]
    assert "sslmode=require" in url
    get_settings.cache_clear()


def test_catalog_env_diag_has_no_secrets(monkeypatch):
    monkeypatch.setenv(
        "CATALOG_DATABASE_URL",
        "postgresql://u:secret-pass@ezv6psql.postgres.database.azure.com:5432/ezofis_catalog_new?sslmode=require",
    )
    from app.config import get_settings
    from app.data_import.catalog import catalog_env_diag, safe_cs_preview

    get_settings.cache_clear()
    diag = catalog_env_diag()
    blob = str(diag)
    assert "secret-pass" not in blob
    assert diag["catalog_target"]["database"] == "ezofis_catalog_new"
    assert diag["catalog_target"]["host"] == "ezv6psql.postgres.database.azure.com"
    preview = safe_cs_preview(
        "Host=ezv6psql.postgres.database.azure.com;Port=5432;Database=ezofis_Tenant_496a0db6;"
        "Username=v6dbadmin;Password=secret-pass;SSL Mode=Require"
    )
    assert preview["tenant_database"] == "ezofis_Tenant_496a0db6"
    assert preview["cs_parse_ok"] is True
    assert "password" not in preview["cs_keys"]
    assert "secret-pass" not in str(preview)
    get_settings.cache_clear()


def test_catalog_unavailable_returns_503(client, monkeypatch):
    from app.data_import.catalog import CatalogUnavailableError

    def boom(_tenant_id, _connection_string=None):
        raise CatalogUnavailableError("Catalog database is unavailable.")

    monkeypatch.setattr("app.data_import.service.create_tenant_engine", boom)
    response = client.post("/api/ezDataImport", json=_VALID_BODY)
    assert response.status_code == 503
    detail = response.json()["detail"]
    if isinstance(detail, dict):
        assert detail["message"] == "Catalog database is unavailable."
        assert "password" not in str(detail).lower()
        assert "AccountKey" not in str(detail)
    else:
        assert detail == "Catalog database is unavailable."


def test_happy_path_monkeypatches_importer(client, monkeypatch):
    def fake_run(request: DataImportRequest, connection_string=None):
        assert request.fileName == "po.xlsx"
        assert UUID(request.tenantId)
        return {"message": "Total rows inserted: 1, Total rows updated: 0"}

    monkeypatch.setattr("app.main.run_data_import", fake_run)
    response = client.post("/api/ezDataImport", json=_VALID_BODY)
    assert response.status_code == 200
    assert response.json() == {"message": "Total rows inserted: 1, Total rows updated: 0"}
    assert "AccountKey" not in response.text
    assert "CATALOG" not in response.text

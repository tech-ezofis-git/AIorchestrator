"""POST /api/ezDataImport — validation and wiring. No live catalog/blob/secrets."""
from uuid import UUID

import pytest

from app.data_import.ident import resolve_import_table_name
from app.data_import.models import DataImportRequest

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


def test_catalog_unavailable_returns_503(client, monkeypatch):
    from app.data_import.catalog import CatalogUnavailableError

    def boom(_tenant_id):
        raise CatalogUnavailableError("Catalog database is unavailable.")

    monkeypatch.setattr("app.data_import.service.create_tenant_engine", boom)
    response = client.post("/api/ezDataImport", json=_VALID_BODY)
    assert response.status_code == 503
    assert response.json()["detail"] == "Catalog database is unavailable."


def test_happy_path_monkeypatches_importer(client, monkeypatch):
    def fake_run(request: DataImportRequest):
        assert request.fileName == "po.xlsx"
        assert UUID(request.tenantId)
        return {"message": "Total rows inserted: 1, Total rows updated: 0"}

    monkeypatch.setattr("app.main.run_data_import", fake_run)
    response = client.post("/api/ezDataImport", json=_VALID_BODY)
    assert response.status_code == 200
    assert response.json() == {"message": "Total rows inserted: 1, Total rows updated: 0"}
    assert "AccountKey" not in response.text
    assert "CATALOG" not in response.text

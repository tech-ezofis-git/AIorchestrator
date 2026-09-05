def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "ezofis_api_base" in body
    assert "ezofis_login_configured" in body
    assert isinstance(body["ezofis_login_configured"], bool)

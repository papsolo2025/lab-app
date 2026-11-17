import pytest

from orders_api.app import create_app


@pytest.fixture()
def client():
    app = create_app("sqlite+pysqlite:///:memory:")
    app.testing = True
    return app.test_client()


def test_liveness(client):
    assert client.get("/health/live").status_code == 200


def test_readiness_with_database(client):
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


def test_readiness_without_database():
    app = create_app("postgresql+psycopg://nobody:wrong@127.0.0.1:1/none?connect_timeout=1")
    resp = app.test_client().get("/health/ready")
    assert resp.status_code == 503


def test_create_and_list_orders(client):
    resp = client.post("/orders", json={"item": "book", "quantity": 2})
    assert resp.status_code == 201
    assert resp.get_json()["item"] == "book"

    orders = client.get("/orders").get_json()
    assert len(orders) == 1
    assert orders[0]["quantity"] == 2


@pytest.mark.parametrize(
    "payload",
    [{}, {"item": ""}, {"item": "pen", "quantity": 0}, {"item": "pen", "quantity": "2"}, {"item": "x" * 101, "quantity": 1}],
)
def test_rejects_invalid_orders(client, payload):
    assert client.post("/orders", json=payload).status_code == 400


def test_metrics_endpoint_counts_requests(client):
    client.get("/health/live")
    body = client.get("/metrics").get_data(as_text=True)
    assert "http_requests_total" in body
    assert 'route="/health/live"' in body


def test_chaos_error_rate(monkeypatch):
    monkeypatch.setenv("CHAOS_ERROR_RATE", "1.0")
    app = create_app("sqlite+pysqlite:///:memory:")
    assert app.test_client().get("/orders").status_code == 500
    assert app.test_client().get("/health/live").status_code == 200

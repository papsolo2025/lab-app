import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orders_api.app import Base, Order
from orders_api.export import export_orders


class FakeS3:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        return {}


def session_factory_with(orders):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        s.add_all(orders)
        s.commit()
    return factory


def test_export_contains_only_last_24_hours():
    now = datetime(2026, 11, 17, 14, 0, tzinfo=timezone.utc)
    factory = session_factory_with([
        Order(item="book", quantity=2, created_at=datetime(2026, 11, 17, 9, 0)),
        Order(item="old", quantity=1, created_at=datetime(2026, 11, 15, 9, 0)),
    ])
    s3 = FakeS3()
    key, count = export_orders(factory, s3, "bucket", "exports/prod", "prod", now=now)

    assert key == "exports/prod/orders-2026-11-17.json"
    assert count == 1
    doc = json.loads(s3.calls[0]["Body"])
    assert doc["orders"][0]["item"] == "book"
    assert s3.calls[0]["ContentType"] == "application/json"


def test_export_matches_the_lambda_contract():
    """The export-processor Lambda requires id:int, item:str, quantity:int for every order."""
    now = datetime.now(timezone.utc)
    factory = session_factory_with([Order(item="pen", quantity=3, created_at=(now - timedelta(hours=1)).replace(tzinfo=None))])
    s3 = FakeS3()
    export_orders(factory, s3, "bucket", "exports/dev/", "dev", now=now)
    order = json.loads(s3.calls[0]["Body"])["orders"][0]
    assert isinstance(order["id"], int) and isinstance(order["item"], str) and isinstance(order["quantity"], int)
    assert s3.calls[0]["Key"].startswith("exports/dev/orders-")

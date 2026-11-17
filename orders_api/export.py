"""Daily orders export to S3 - run by a Kubernetes CronJob.

    python -m orders_api.export

Writes s3://$EXPORT_BUCKET/exports/<namespace>/orders-YYYY-MM-DD.json with the last 24 hours of orders.
AWS credentials come from EKS Pod Identity (service account orders-export) - no keys anywhere.
The S3 upload triggers the export-processor Lambda in lab-serverless.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from orders_api.app import Base, Order, database_url_from_env


def build_export(session_factory, environment, now):
    since = now - timedelta(days=1)
    with session_factory() as session:
        rows = session.scalars(
            select(Order).where(Order.created_at >= since.replace(tzinfo=None)).order_by(Order.id)
        ).all()
        orders = [
            {"id": o.id, "item": o.item, "quantity": o.quantity,
             "created_at": o.created_at.isoformat() if o.created_at else None}
            for o in rows
        ]
    return {
        "environment": environment,
        "generated_at": now.isoformat(),
        "window_start": since.isoformat(),
        "orders": orders,
    }


def export_orders(session_factory, s3, bucket, prefix, environment, now=None):
    now = now or datetime.now(timezone.utc)
    doc = build_export(session_factory, environment, now)
    key = f"{prefix.rstrip('/')}/orders-{now:%Y-%m-%d}.json"
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(doc).encode(), ContentType="application/json")
    return key, len(doc["orders"])


def main():
    import boto3  # imported here so the web app does not need it at startup

    bucket = os.environ["EXPORT_BUCKET"]
    prefix = os.environ["EXPORT_PREFIX"]  # exports/<namespace> - IAM only allows the pod's own namespace
    environment = os.environ.get("APP_ENV", "unknown")

    engine = create_engine(database_url_from_env(), pool_pre_ping=True)
    Base.metadata.create_all(engine)
    key, count = export_orders(sessionmaker(bind=engine), boto3.client("s3"), bucket, prefix, environment)
    print(json.dumps({"event": "export_written", "bucket": bucket, "key": key, "orders": count}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

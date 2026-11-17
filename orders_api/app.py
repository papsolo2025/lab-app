"""orders-api: a small service used to practice CI/CD, Kubernetes, monitoring and SRE work.

Endpoints
  GET  /               service info
  GET  /health/live    liveness  (process is up)
  GET  /health/ready   readiness (database reachable)
  GET  /orders         list the latest orders
  POST /orders         create an order  {"item": "book", "quantity": 2}
  GET  /metrics        Prometheus metrics

Chaos settings (environment variables) used for alerting and SLO drills:
  CHAOS_ERROR_RATE   0.0-1.0  share of /orders requests that return HTTP 500
  CHAOS_LATENCY_MS   extra delay added to /orders requests
"""
import os
import random
import time

from flask import Flask, Response, g, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import Column, DateTime, Integer, String, create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

Base = declarative_base()


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    item = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


def database_url_from_env():
    """Build the DB URL from separate variables so passwords with special characters are safe."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("DB_NAME", "orders"),
        query={"sslmode": os.environ.get("DB_SSLMODE", "require")},
    )


def create_app(database_url=None):
    app = Flask(__name__)
    url = database_url or database_url_from_env()

    if str(url).startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    Session = sessionmaker(bind=engine)
    state = {"schema_ready": False}

    def ensure_schema():
        # Lab shortcut: create tables on first use. Production would use migrations (e.g. Alembic).
        if not state["schema_ready"]:
            Base.metadata.create_all(engine)
            state["schema_ready"] = True

    error_rate = float(os.environ.get("CHAOS_ERROR_RATE", "0"))
    extra_latency = int(os.environ.get("CHAOS_LATENCY_MS", "0")) / 1000

    @app.before_request
    def start_timer():
        g.start = time.perf_counter()
        if request.path.startswith("/orders"):
            if extra_latency:
                time.sleep(extra_latency)
            if error_rate and random.random() < error_rate:
                return jsonify(error="chaos: injected failure"), 500
        return None

    @app.after_request
    def record_metrics(response):
        route = request.url_rule.rule if request.url_rule else "unmatched"
        if route != "/metrics":
            elapsed = time.perf_counter() - g.get("start", time.perf_counter())
            LATENCY.labels(request.method, route).observe(elapsed)
            REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        return response

    @app.get("/")
    def info():
        return jsonify(
            service="orders-api",
            version=os.environ.get("APP_VERSION", "dev"),
            environment=os.environ.get("APP_ENV", "local"),
        )

    @app.get("/health/live")
    def live():
        return jsonify(status="ok")

    @app.get("/health/ready")
    def ready():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            ensure_schema()
            return jsonify(status="ready")
        except Exception as exc:  # noqa: BLE001 - report any DB failure as not ready
            return jsonify(status="not ready", error=type(exc).__name__), 503

    @app.get("/orders")
    def list_orders():
        ensure_schema()
        with Session() as session:
            rows = session.scalars(select(Order).order_by(Order.id.desc()).limit(50)).all()
            return jsonify([{"id": o.id, "item": o.item, "quantity": o.quantity} for o in rows])

    @app.post("/orders")
    def create_order():
        data = request.get_json(silent=True) or {}
        item = str(data.get("item", "")).strip()
        quantity = data.get("quantity")
        if not item or len(item) > 100:
            return jsonify(error="item is required (max 100 characters)"), 400
        if not isinstance(quantity, int) or not 1 <= quantity <= 1000:
            return jsonify(error="quantity must be an integer from 1 to 1000"), 400
        ensure_schema()
        with Session() as session:
            order = Order(item=item, quantity=quantity)
            session.add(order)
            session.commit()
            return jsonify(id=order.id, item=order.item, quantity=order.quantity), 201

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    return app

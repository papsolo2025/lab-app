# ---- build stage: install dependencies ----
FROM python:3.13-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime stage: small image, non-root user ----
FROM python:3.13-slim
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY --from=build /install /usr/local
COPY orders_api/ orders_api/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001
EXPOSE 8080

# One worker + threads keeps Prometheus metrics simple (single process).
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:8080", \
     "--access-logfile", "-", "--worker-tmp-dir", "/tmp", "orders_api.app:create_app()"]

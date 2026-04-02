FROM python:3.11-slim

WORKDIR /app

# System dependencies for ONNX and PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY alembic.ini .
COPY alembic/ alembic/
COPY src/ src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "jarvis.main:app", "--host", "0.0.0.0", "--port", "8000"]

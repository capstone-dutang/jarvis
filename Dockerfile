FROM python:3.11-slim

WORKDIR /app

# System dependencies for ONNX and PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Pre-download the embedding model (dragonkue/multilingual-e5-small-ko, ONNX)
# into the image. Without this the model is fetched at runtime on the first
# recall, so a fresh container would fall back to ILIKE until ~466MB downloads.
# Baking it makes recall work on the first request — and makes the image
# portable to a new server with no warm-up. Keep in sync with
# config.Settings.embedding_model_name.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('dragonkue/multilingual-e5-small-ko', backend='onnx')"

COPY alembic.ini .
COPY alembic/ alembic/
COPY src/ src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "jarvis.main:app", "--host", "0.0.0.0", "--port", "8000"]

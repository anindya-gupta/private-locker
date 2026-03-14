FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlcipher-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements-deploy.txt requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY . .
RUN pip install --no-cache-dir --prefix=/install -e .

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlcipher-dev \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY --from=builder /build /app

WORKDIR /app

ENV VAULT_DIR=/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["vault", "serve", "--host", "0.0.0.0", "--port", "8080"]

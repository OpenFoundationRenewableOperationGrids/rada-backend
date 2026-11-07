FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY bridge/requirements.txt bridge/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r bridge/requirements.txt

COPY *.py .
# The OpenEMS bridge runs from the same image as a second service:
#   command: python -m bridge
COPY bridge/ bridge/

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

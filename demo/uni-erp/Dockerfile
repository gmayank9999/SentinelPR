FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UNIERP_FAULT=none

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn pydantic

COPY unierp ./unierp

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=2s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "unierp.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKFLOW_RUNTIME_DIR=/app/web/runtime

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY web /app/web

RUN groupadd --gid 10001 workflow \
    && useradd --uid 10001 --gid workflow --no-create-home workflow \
    && mkdir -p /app/web/runtime/outputs /app/web/runtime/uploads \
    && chown -R workflow:workflow /app/web/runtime

USER workflow

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]

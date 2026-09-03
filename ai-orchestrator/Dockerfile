FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY ai-orchestrator/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY ai-orchestrator /app/ai-orchestrator
COPY testdata /app/testdata
COPY deployments/e2e/mineru-e2e /app/mineru-e2e
RUN chmod 0755 /app/mineru-e2e
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/ai-orchestrator", "--host", "0.0.0.0", "--port", "8090"]

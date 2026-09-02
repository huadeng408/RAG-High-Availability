FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY ai-orchestrator/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY ai-orchestrator /app/ai-orchestrator
COPY testdata /app/testdata
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/ai-orchestrator", "--host", "0.0.0.0", "--port", "8090"]

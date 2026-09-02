FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir fastapi==0.117.1 uvicorn==0.37.0 pydantic==2.11.9
COPY deployments/e2e/model_stub.py /app/model_stub.py
COPY testdata/rha_image_fixture.json /app/rha_image_fixture.json
EXPOSE 8010
CMD ["python", "-m", "uvicorn", "model_stub:app", "--host", "0.0.0.0", "--port", "8010"]

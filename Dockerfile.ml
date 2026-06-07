FROM python:3.12-slim

WORKDIR /app

COPY ml_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ml_service/ ./ml_service/

CMD ["uvicorn", "ml_service.main:app", "--host", "0.0.0.0", "--port", "8001"]

FROM python:3.13-slim

WORKDIR /app

COPY scheduler/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY scheduler/app/ ./scheduler_app/
COPY crawler/app/ ./crawler_app/

ENV PYTHONPATH=/app/backend:/app/scheduler_app:/app/crawler_app

CMD ["python", "/app/scheduler_app/main.py"]

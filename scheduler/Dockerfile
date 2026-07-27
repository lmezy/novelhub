FROM python:3.13-slim

WORKDIR /app

COPY backend/requirements.txt ./backend_req.txt
COPY scheduler/requirements.txt ./scheduler_req.txt
RUN pip install --no-cache-dir -r backend_req.txt -r scheduler_req.txt

COPY backend/ ./backend/
COPY scheduler/app/ ./scheduler_app/
COPY crawler/app/ ./crawler_app/

ENV PYTHONPATH=/app/backend:/app/scheduler_app:/app/crawler_app

CMD ["python", "/app/scheduler_app/main.py"]

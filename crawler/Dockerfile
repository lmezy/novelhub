FROM python:3.13-slim

WORKDIR /app

COPY backend/requirements.txt ./backend_req.txt
COPY crawler/requirements.txt ./crawler_req.txt
RUN pip install --no-cache-dir -r backend_req.txt -r crawler_req.txt

COPY backend/ ./backend/
COPY crawler/app/ ./crawler_app/

ENV PYTHONPATH=/app/backend:/app/crawler_app

CMD ["python", "/app/crawler_app/main.py"]

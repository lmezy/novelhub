FROM python:3.13-slim

WORKDIR /app

COPY crawler/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY crawler/app/ ./crawler_app/

ENV PYTHONPATH=/app/backend:/app/crawler_app

CMD ["python", "/app/crawler_app/main.py"]

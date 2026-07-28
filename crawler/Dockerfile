FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Install Python deps only -- Playwright + Chromium + system libs are already in base image
COPY backend/requirements.txt ./backend_req.txt
COPY crawler/requirements.txt ./crawler_req.txt
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r backend_req.txt \
    -r crawler_req.txt

COPY backend/ ./backend/
COPY scheduler/app/ ./scheduler_app/
COPY crawler/app/ ./crawler_app/

ENV PYTHONPATH=/app/backend:/app/crawler_app:/app/scheduler_app

CMD ["python", "/app/crawler_app/main.py"]

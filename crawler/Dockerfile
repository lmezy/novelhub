FROM python:3.13-slim

WORKDIR /app

# Install Chromium system dependencies for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk-bridge2.0-0t64 libatk1.0-0t64 \
    libcups2t64 libdrm2 libdbus-1-3 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2t64 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt ./backend_req.txt
COPY crawler/requirements.txt ./crawler_req.txt
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r backend_req.txt \
    -r crawler_req.txt

# Install Chromium browser for Playwright (use Taobao mirror for China)
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
RUN python -m playwright install chromium

COPY backend/ ./backend/
COPY scheduler/app/ ./scheduler_app/
COPY crawler/app/ ./crawler_app/

ENV PYTHONPATH=/app/backend:/app/crawler_app:/app/scheduler_app

CMD ["python", "/app/crawler_app/main.py"]

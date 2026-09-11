# Playwright official image: Python + Chromium + all deps pre-installed
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Playwright and Chromium are already in the base image.
# Node.js powers the YueDu JS rule runtime (org.jsoup shim).
# Ubuntu's default archive is unreliable from mainland China, where single
# archive/security IPs often time out; point apt at the Tsinghua mirror.
RUN sed -i 's|//archive.ubuntu.com|//mirrors.tuna.tsinghua.edu.cn|g; s|//security.ubuntu.com|//mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Only need to install our Python dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic

CMD ["sh","-c","alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop asyncio"]

# Playwright official image: Python + Chromium + all deps pre-installed
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Playwright and Chromium are already in the base image.
# Only need to install our Python dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic

CMD ["sh","-c","alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

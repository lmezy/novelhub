FROM python:3.13-slim

WORKDIR /app

# Node.js powers the YueDu JS rule runtime (org.jsoup shim) and curl is used
# by that shim's synchronous java.ajax/post/get helpers.
# Use a China mirror for Debian packages so builds don't stall on deb.debian.org
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list \
        /etc/apt/sources.list.d/debian.sources \
    2>/dev/null || true \
    && export DEBIAN_FRONTEND=noninteractive \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend_req.txt
COPY scheduler/requirements.txt ./scheduler_req.txt
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r backend_req.txt \
    -r scheduler_req.txt

COPY backend/ ./backend/
COPY scheduler/app/ ./scheduler_app/
COPY crawler/app/ ./crawler_app/

ENV PYTHONPATH=/app/backend:/app/scheduler_app:/app/crawler_app

CMD ["python", "/app/scheduler_app/main.py"]

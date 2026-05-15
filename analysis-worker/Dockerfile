FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config

# ── 可选: Playwright + Chromium (Instagram 浏览器模拟，封号风险低) ──
# 取消下面注释来安装 Playwright 浏览器（镜像增加 ~500MB）:
# RUN playwright install chromium --with-deps
# ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

EXPOSE 8090

CMD uvicorn app.main:app --host 0.0.0.0 --port 8090

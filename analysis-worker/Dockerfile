FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DOCKER_ENV=1

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY tests ./tests
RUN mkdir -p /app/data /app/reports

# Install Chromium runtime for Playwright by default.
# Tradeoff: larger image, but the Instagram Playwright collector works in Docker.
ARG PLAYWRIGHT_INSTALL_CHROMIUM=true
RUN if [ "$PLAYWRIGHT_INSTALL_CHROMIUM" = "true" ]; then playwright install chromium --with-deps; fi

EXPOSE 8090

CMD uvicorn app.main:app --host 0.0.0.0 --port 8090

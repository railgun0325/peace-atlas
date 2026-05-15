FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DOCKER_ENV=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
RUN mkdir -p /app/data /app/reports

# Optional: install Chromium runtime for Playwright during image build.
# Enabled with --build-arg PLAYWRIGHT_INSTALL_CHROMIUM=true
# Tradeoff: larger image, but full Playwright flow works in container.
ARG PLAYWRIGHT_INSTALL_CHROMIUM=false
RUN if [ "$PLAYWRIGHT_INSTALL_CHROMIUM" = "true" ]; then playwright install chromium --with-deps; fi
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

EXPOSE 8090

CMD uvicorn app.main:app --host 0.0.0.0 --port 8090

FROM diygod/rsshub:latest

USER root

# Some public RSSHub routes use Playwright. The upstream image can run RSSHub
# itself but may not include the browser binary, which makes those routes fail
# with "Executable doesn't exist". Install only Chromium here.
RUN npx playwright install --with-deps chromium

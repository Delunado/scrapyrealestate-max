FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="scrapyrealestate" \
      org.opencontainers.image.description="Persistent Spanish real-estate search monitor" \
      org.opencontainers.image.licenses="GPL-3.0"

# Keep runtime data and Playwright browsers outside the application source tree.
# The data location is also the target of the Compose-managed persistent volume.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Madrid \
    SCRAPYREALESTATE_DATA_DIR=/var/lib/scrapyrealestate \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/home/scrapyrealestate

# curl backs the container healthcheck and tini reaps Scrapy/Chromium children.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash curl tzdata tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /scrapyrealestate/scrapyrealestate

# Install dependencies before copying the source so dependency layers stay cached.
COPY scrapyrealestate/requirements.txt ./requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Install the single browser used by Playwright-enabled spiders while still root.
RUN playwright install --with-deps chromium

# A fixed UID/GID makes bind-mount ownership predictable. The browser directory,
# application source, home directory and mounted-volume target are all usable by it.
RUN groupadd --gid 10001 scrapyrealestate \
    && useradd --uid 10001 --gid scrapyrealestate --create-home \
       --home-dir /home/scrapyrealestate --shell /usr/sbin/nologin scrapyrealestate \
    && install -d --owner=scrapyrealestate --group=scrapyrealestate \
       /var/lib/scrapyrealestate \
    && chown -R scrapyrealestate:scrapyrealestate /ms-playwright

COPY --chown=scrapyrealestate:scrapyrealestate scrapyrealestate/ ./

USER scrapyrealestate

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl --fail --silent --show-error http://127.0.0.1:8080/readyz || exit 1

# tini remains PID 1 for direct `docker run` usage and reaps browser/spider children.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "main.py"]

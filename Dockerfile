FROM python:3.12-slim

RUN pip install --no-cache-dir -r requirements.txt

ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.46/supercronic-linux-amd64 \
    SUPERCRONIC_SHA256=5adff01c5a797663948e656d2b61d10932369ee437eb5cb54fa872b2960f222b \
    SUPERCRONIC=supercronic-linux-amd64

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSLO "$SUPERCRONIC_URL" \
    && echo "${SUPERCRONIC_SHA256}  ${SUPERCRONIC}" | sha256sum -c - \
    && chmod +x "$SUPERCRONIC" \
    && mv "$SUPERCRONIC" "/usr/local/bin/${SUPERCRONIC}" \
    && ln -s "/usr/local/bin/${SUPERCRONIC}" /usr/local/bin/supercronic \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

COPY crontab /etc/crontab

CMD ["supercronic", "/etc/crontab"]

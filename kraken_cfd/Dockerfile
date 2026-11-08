FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Market data, indicators, cost tracking and the Home Assistant client are shared with the Kraken bot add-on.
# Home Assistant builds each add-on from its own folder only, so the package is taken from the repository at
# a fixed commit (bump it to pick up changes in kraken_bot/krakenbot).
ARG KRAKENBOT_REF=0fa1580aeb9deb04eba0bc100f3a78b832caf8ad
ADD https://github.com/dadamus/kraken-bot/archive/${KRAKENBOT_REF}.tar.gz /tmp/kraken-bot.tar.gz
RUN tar -xzf /tmp/kraken-bot.tar.gz -C /tmp \
    && mv /tmp/kraken-bot-${KRAKENBOT_REF}/kraken_bot/krakenbot ./krakenbot \
    && rm -rf /tmp/kraken-bot.tar.gz /tmp/kraken-bot-${KRAKENBOT_REF}
COPY cfd_bot.py addon.py config.example.json ./
COPY cfd ./cfd
ENV CFD_DATA_DIR=/config
VOLUME /config
CMD ["python3", "-u", "addon.py"]

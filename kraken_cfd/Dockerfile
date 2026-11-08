FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# krakenbot is a copy of kraken_bot/krakenbot (market data, indicators, costs, Home Assistant client):
# Home Assistant builds each add-on from its own folder only. Keep it in sync with scripts/sync_krakenbot.py.
COPY krakenbot ./krakenbot
COPY cfd_bot.py addon.py config.example.json ./
COPY cfd ./cfd
ENV CFD_DATA_DIR=/config
VOLUME /config
CMD ["python3", "-u", "addon.py"]

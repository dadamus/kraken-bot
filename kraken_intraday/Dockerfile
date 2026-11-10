FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# krakenbot is a copy of kraken_bot/krakenbot (market data, Home Assistant client) and cfd a copy of
# kraken_cfd/cfd (the simulated CFD account): Home Assistant builds each add-on from its own folder only.
# Keep them in sync with scripts/sync_krakenbot.py.
COPY krakenbot ./krakenbot
COPY cfd ./cfd
COPY intraday_bot.py addon.py config.example.json ./
COPY intraday ./intraday
ENV INTRADAY_DATA_DIR=/config
VOLUME /config
CMD ["python3", "-u", "addon.py"]

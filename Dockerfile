FROM python:3.12-slim

WORKDIR /app

COPY app.py requirements.txt index.html ./

RUN apt-get update && apt-get install -y --no-install-recommends openssl bash curl && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir -r requirements.txt

ENV PORT=8080
EXPOSE 8080

CMD ["python3", "app.py"]

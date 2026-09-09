FROM python:3.12-alpine

RUN apk add --no-cache \
    bash \
    curl \
    openssl \
    ca-certificates \
    && update-ca-certificates

WORKDIR /app

COPY app.py requirements.txt index.html ./

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 3000

CMD ["python3", "app.py"]

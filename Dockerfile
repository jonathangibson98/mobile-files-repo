FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends samba samba-common-bin procps tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DATA_DIR=/data CONFIG_DIR=/config PORT=8080 PUID=1000 PGID=1000
VOLUME ["/data", "/config", "/var/lib/samba"]
EXPOSE 8080 445 139

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]

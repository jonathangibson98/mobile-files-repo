FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/jonathangibson98/mobile-files-repo" \
      org.opencontainers.image.description="Phone-friendly file manager, text editor and Samba admin"

RUN apt-get update \
 && apt-get install -y --no-install-recommends samba samba-common-bin procps tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY mobile_files/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mobile_files/app/ /app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DATA_DIR=/data CONFIG_DIR=/config PORT=8080 PUID=1000 PGID=1000
VOLUME ["/data", "/config", "/var/lib/samba"]
EXPOSE 8080 445 139

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/me' % os.environ.get('PORT','8080'),timeout=4)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]

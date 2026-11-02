#!/usr/bin/with-contenv bashio
set -e

export ADMIN_USER="$(bashio::config 'admin_user')"
export ADMIN_PASSWORD="$(bashio::config 'admin_password')"
export PUID="$(bashio::config 'puid')"
export PGID="$(bashio::config 'pgid')"
export SAMBA_ENABLED=0
bashio::config.true 'samba_enabled' && SAMBA_ENABLED=1
export PORT=8080

# Files live under /share (shown as the "files" share). Settings persist in /config
# (= /addon_configs/<slug>/ on the host) and the Samba password DB is kept there too.
export DATA_DIR=/share
export CONFIG_DIR=/config
mkdir -p /share /config/samba-db /var/log/samba /run/samba

# Persist Samba's password database across restarts/updates
rm -rf /var/lib/samba
ln -sfn /config/samba-db /var/lib/samba
mkdir -p /var/lib/samba/private

if [ "$SAMBA_ENABLED" = "1" ]; then
  getent group files >/dev/null || groupadd -o -g "$PGID" files
  id files >/dev/null 2>&1 || useradd -o -M -N -u "$PUID" -g files -s /sbin/nologin files
fi

bashio::log.info "Restoring config..."
python /app/main.py restore

PIDS=""
if [ "$SAMBA_ENABLED" = "1" ]; then
  nmbd --foreground --no-process-group &
  PIDS="$PIDS $!"
  smbd --foreground --no-process-group &
  PIDS="$PIDS $!"
fi

term() { kill $PIDS $WEB 2>/dev/null || true; }
trap term TERM INT

gunicorn --bind "0.0.0.0:${PORT}" --workers 1 --threads 8 --timeout 3600 \
  --chdir /app main:app &
WEB=$!

wait -n $PIDS $WEB
term
wait

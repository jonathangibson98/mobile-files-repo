#!/usr/bin/with-contenv bashio
set -e

OPTS=/data/options.json

export ADMIN_USER="$(bashio::config 'admin_user')"
export ADMIN_PASSWORD="$(bashio::config 'admin_password')"
export PUID="$(bashio::config 'puid')"
export PGID="$(bashio::config 'pgid')"
export SAMBA_ENABLED=0
bashio::config.true 'samba_enabled' && SAMBA_ENABLED=1
export PORT=8080

ROOT_PATH="$(bashio::config 'root_path')"
case "$ROOT_PATH" in /*) ;; *) bashio::log.warning "root_path must start with '/', using '/'"; ROOT_PATH=/ ;; esac
export DATA_DIR="$ROOT_PATH"

export HIDE_WEB_JSON="$(jq -c '.hide_from_web // []' "$OPTS")"
export HIDE_SAMBA_JSON="$(jq -c '.hide_from_samba // []' "$OPTS")"
bashio::log.info "Root folder: ${DATA_DIR}"
bashio::log.info "Hidden in web UI: ${HIDE_WEB_JSON}"
bashio::log.info "Hidden in Samba (names only): ${HIDE_SAMBA_JSON}"

# The add-on's own persistent storage (settings, Samba users). /config is Home Assistant's config.
export CONFIG_DIR=/data
mkdir -p /data/samba-db /var/log/samba /run/samba

# Persist Samba's password database across restarts/updates
rm -rf /var/lib/samba
ln -sfn /data/samba-db /var/lib/samba
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

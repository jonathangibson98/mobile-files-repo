#!/bin/bash
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
SAMBA_ENABLED="${SAMBA_ENABLED:-1}"

# Settings saved from the web UI (Settings tab) win over the environment after the first start.
if [ -f /config/config.json ]; then
  SAVED_SAMBA="$(python -c 'import json,sys; d=json.load(open("/config/config.json")).get("docker",{}); v=d.get("samba_enabled"); print("" if v is None else ("1" if v else "0"))' 2>/dev/null || true)"
  [ -n "$SAVED_SAMBA" ] && SAMBA_ENABLED="$SAVED_SAMBA"
fi
export SAMBA_ENABLED

mkdir -p /data /config /var/log/samba /var/lib/samba/private /run/samba

if [ "$SAMBA_ENABLED" = "1" ]; then
  # Service account that owns every file created via web UI or Samba.
  getent group files >/dev/null || groupadd -o -g "$PGID" files
  id files >/dev/null 2>&1 || useradd -o -M -N -u "$PUID" -g files -s /usr/sbin/nologin files
  # `files` also needs a Samba identity so force user works cleanly.
  chown "$PUID:$PGID" /data 2>/dev/null || true
fi

# Rebuild users + smb.conf from /config/config.json (survives container recreation)
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

gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 1 --threads 8 --timeout 3600 \
  --chdir /app main:app &
WEB=$!

wait -n $PIDS $WEB
term
wait

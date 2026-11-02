# mobile-files

A self-contained, phone-friendly web UI that combines:

- **File manager** – browse, upload, download, rename, move, delete.
- **Samba admin** – add/edit/remove shares, set guest/read-only/allowed users, manage Samba users and passwords, view active connections. Changes are validated with `testparm` and applied live.

No File Browser dependency. One container, run with Docker Compose.

## Run

```bash
git clone https://github.com/jonathangibson98/mobile-files
cd mobile-files
# edit ADMIN_PASSWORD in docker-compose.yml first
docker compose up -d --build
```

- Web UI: `http://<host>:8080`
- SMB: `\\<host>\files` (Windows) / `smb://<host>/files` (macOS, iOS, Android)

By default there is one share, `files`, pointing at `/data` (mounted from `./data`). Create Samba users and tweak shares in the **Samba** tab.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ADMIN_USER` | `admin` | Web UI username |
| `ADMIN_PASSWORD` | generated, printed in logs | Web UI password |
| `PUID` / `PGID` | `1000` | Owner of all files created via UI or Samba |
| `SAMBA_ENABLED` | `1` | `0` = file manager only |
| `PORT` | `8080` | Web UI port |

Volumes: `/data` (your files), `/config` (settings, `config.json`), `/var/lib/samba` (Samba password DB).

## Notes

- Share folders must live inside `/data`.
- Samba users are Samba-only accounts (no shell, no login). A share with no "allowed users" selected accepts any valid Samba user; tick "guest" for passwordless access.
- The web UI is plain HTTP. Put it behind a reverse proxy with TLS (Caddy, Traefik, nginx) if exposed beyond your LAN, and don't forward port 445 to the internet.
- For discovery in Windows/macOS network browsers, uncomment `network_mode: host` in the compose file.

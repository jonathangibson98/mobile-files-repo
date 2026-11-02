# mobile-files

A self-contained, phone-friendly web UI that combines:

- **File manager** – browse, upload, download, rename, move, delete.
- **Samba admin** – add/edit/remove shares, set guest/read-only/allowed users, manage Samba users and passwords, view active connections. Changes are validated with `testparm` and applied live.

No File Browser dependency. Run it with Docker Compose, or install it as a Home Assistant add-on.

## Run (Docker Compose)

```bash
git clone https://github.com/jonathangibson98/mobile-files
cd mobile-files
# edit ADMIN_PASSWORD in docker-compose.yml first
docker compose up -d --build
```

- Web UI: `http://<host>:8080`
- SMB: `\\<host>\files` (Windows) / `smb://<host>/files` (macOS, iOS, Android)

By default there is one share, `files`, pointing at `/data` (mounted from `./data`). Create Samba users and tweak shares in the **Samba** tab.

## Run (Home Assistant add-on)

1. In Home Assistant go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
2. Add `https://github.com/jonathangibson98/mobile-files`.
3. Install **Mobile Files**, start it, and open **Files** from the sidebar (ingress, no extra login).

The add-on sees the whole filesystem (`/config`, `/share`, `/media`, `/backup`, `/ssl`, `/addons`, `/addon_configs`) and can view, edit, rename, move and delete anything. Add-on options: `root_path`, `hide_from_web`, `hide_from_samba`, `admin_user`, `admin_password` (only for direct access on port 8080), `samba_enabled`, `puid`, `pgid`. See [`mobile_files/DOCS.md`](mobile_files/DOCS.md).

## Repo layout

- `Dockerfile`, `docker-compose.yml`, `entrypoint.sh` – Docker version (builds from `mobile_files/app/`)
- `repository.yaml`, `mobile_files/` – Home Assistant add-on (also holds the shared app code in `mobile_files/app/`)

## Configuration (Docker)

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

# mobile-files

A self-contained, phone-friendly web UI that combines:

- **File manager**: browse, search, sort, upload (files or whole folders, drag & drop on desktop), download (folders as ZIP), copy, duplicate, rename, move, delete.
- **Trash**: deletes from the web UI *and* from Samba go to a Trash you can restore from. Old items are emptied automatically after a set number of days.
- **Viewer & editor**: swipe through photos and videos, play audio, read PDFs, and edit text/YAML/JSON with save-conflict detection.
- **Organize**: favorite folders (pinned chips, synced across devices), zip/unzip archives, folder info (size, file count, permissions), long-press to multi-select.
- **Samba admin**: add/edit/remove shares, guest/read-only/allowed users, Samba users and passwords, live connections, copy-paste connect addresses, optional macOS/iOS optimizations. Changes are validated with `testparm` and applied live.

The same app runs as a plain Docker container or as a Home Assistant add-on, with the same UI and features in both.

## Run (Docker Compose)

```bash
git clone https://github.com/jonathangibson98/mobile-files-repo
cd mobile-files-repo
# edit ADMIN_PASSWORD in docker-compose.yml first
docker compose pull && docker compose up -d
```

To build the image yourself instead, swap `image:` for `build: .` in the compose file and run `docker compose up -d --build`.

- Web UI: `http://<host>:8080` (can be added to your phone's home screen)
- SMB: `\\<host>\files` (Windows) / `smb://<host>/files` (macOS, iOS, Android)

By default there is one share, `files`, pointing at `/data` (mounted from `./data`). Create Samba users and tweak shares in the **Samba** tab.

**Updating:** `docker compose pull && docker compose up -d`. Settings, users and Trash are kept.

## Run (Home Assistant add-on)

1. In Home Assistant go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
2. Add `https://github.com/jonathangibson98/mobile-files-repo`.
3. Install **Mobile Files & Samba**, start it, and open it from the sidebar (ingress, no extra login).

The add-on sees the whole filesystem (`/config`, `/share`, `/media`, `/backup`, `/ssl`, `/addons`, `/addon_configs`) and can view, edit, rename, move and delete anything. See [`mobile_files/DOCS.md`](mobile_files/DOCS.md) for the options.

## Configuration (Docker)

| Env var | Default | Purpose |
|---|---|---|
| `ADMIN_USER` | `admin` | Web UI username |
| `ADMIN_PASSWORD` | generated, printed in logs | Web UI password |
| `PUID` / `PGID` | `1000` | Owner of all files created via UI or Samba |
| `SAMBA_ENABLED` | `1` | `0` = file manager only |
| `HIDE_FROM_WEB` | empty | Comma-separated paths/names/wildcards hidden **and blocked** in the web UI (`*.db, /private, .cache`) |
| `HIDE_FROM_SAMBA` | empty | Comma-separated names hidden in Samba listings |
| `TRASH_DIR` | `/data/.mobile_files_trash` | Where deleted items go. Set to empty to make deletes permanent |
| `PORT` | `8080` | Web UI port |

Volumes: `/data` (your files), `/config` (settings, `config.json`), `/var/lib/samba` (Samba password DB).

These match the add-on options (`hide_from_web`, `hide_from_samba`, `trash_path`, …), so both versions behave the same.

**Docker: change these in the web UI.** Open the **Settings** tab to edit the hide lists, the Trash folder and the Samba switch. They're saved in `/config/config.json` and win over the environment variables, which only fill them in on the first start. Hide lists and Trash apply right away; turning Samba on or off applies after a container restart. (In the Home Assistant add-on these stay on the add-on's Configuration tab.)

## Repo layout

- `mobile_files/app/`: the app (Flask API + single-page UI), shared by both versions
- `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`: Docker version
- `repository.yaml`, `mobile_files/`: Home Assistant add-on
- `.github/workflows/publish-ghcr.yml`: builds both images on a `v*` tag

## Releasing

Bump `version` in `mobile_files/config.yaml` and `VERSION` in `mobile_files/app/main.py`, add a `CHANGELOG.md` entry, then push a matching tag (`git tag v2.0.1 && git push origin v2.0.1`). That rebuilds **both** the add-on images and the Docker image (`latest`, `2.0.1`, `2.0`).

## Notes

- Share folders must live inside `/data`.
- Samba users are Samba-only accounts (no shell, no login). A share with no "allowed users" selected accepts any valid Samba user; tick "guest" for passwordless access.
- The Samba recycle bin skips temp and lock files (`*.tmp`, `~$*`, `.DS_Store`, …).
- The web UI is plain HTTP. Put it behind a reverse proxy with TLS (Caddy, Traefik, Nginx Proxy Manager) if exposed beyond your LAN, and don't forward port 445 to the internet.
- For discovery in Windows/macOS network browsers, uncomment `network_mode: host` in the compose file.

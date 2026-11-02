# Mobile Files

Open it from **Files** in the Home Assistant sidebar (ingress, no extra login; the panel is admin-only).

## Access
- **Root folder** (`root_path`, default `/`) is the top of the file manager and of the default Samba share `files`. With `/` you can browse everything the add-on can see: `/config` (Home Assistant config), `/share`, `/media`, `/backup`, `/ssl`, `/addons`, `/addon_configs`, and the add-on's own container filesystem.
- Files are opened as root: you can view, edit (tap ⋯ → Edit), rename, move, upload, download and delete anything, including read-only files. Only the root folder itself can't be deleted or renamed.
- Be careful: `/data` holds this add-on's options (including passwords). Hide it with `hide_from_web: /data` if you want.

## Hiding files
`hide_from_web` hides **and blocks** entries in the web UI (listing, download, edit, rename, move, delete). One per line:
- full path: `/config/secrets.yaml`
- name matched anywhere: `.storage`
- wildcards: `*.db`, `/config/*.bak`, `/config/**/secrets*` (`*` = within a folder, `**` = across folders)

`hide_from_samba` hides names in Samba listings. Samba can only match names, not paths, so `secrets.yaml` is hidden in every share. Hidden items can still be opened if someone types the exact path. Restart the add-on after changing either list.

## Samba
- `smb://<ha-host>/files`. Create Samba users in the **Samba** tab.
- If Samba can't bind, make sure nothing else (e.g. the Samba share add-on) uses port 445.
- `admin_password` is only needed if you open the UI directly on port 8080 (not exposed by default).

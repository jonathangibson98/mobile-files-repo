# Mobile Files

Open it from the Home Assistant sidebar (ingress, no extra login; the panel is admin-only).

## Using it
- **Select**: tap ☑ or long-press a file. Checkboxes and the selection bar (Copy, Move, Zip, Download, Delete) only appear in select mode.
- **⋯ menu**: Open, Edit as text, Download (folders as ZIP), Info, Rename, Duplicate, Copy to…, Move to…, Compress to ZIP, Extract here, Add to favorites, Delete.
- **Search** searches the current folder and everything below it. `*` and `?` wildcards work (`*.yaml`).
- **⇅** sorts by name, date, size or type, and shows or hides dot-files. This is remembered per device.
- **☆** in the path bar pins a folder as a favorite chip. Favorites are saved on the server, so they show on every device.
- **＋ New**: new folder, new text file, upload files, or upload a whole folder.
- Photos and videos open in a viewer. Swipe or use the arrows to go through the folder.
- The editor warns you if the file changed on disk since you opened it (e.g. Home Assistant rewrote it). Ctrl/⌘+S saves.

## Trash
Deleting something moves it to **Trash** (`trash_path`, default `/share/.mobile_files_trash`). Files deleted over Samba go there too when *Recycle bin* is on in the Samba tab. In the Trash tab you can restore items to where they were, delete them forever, or set how many days to keep them (default 30, `0` = forever). Clear `trash_path` to make deletes permanent.

## Access
- **Root folder** (`root_path`, default `/`) is the top of the file manager and of the default Samba share `files`. With `/` you can browse everything the add-on can see: `/config` (Home Assistant config), `/share`, `/media`, `/backup`, `/ssl`, `/addons`, `/addon_configs`, and the add-on's own container filesystem.
- Files are opened as root: you can view, edit, rename, move, upload, download and delete anything, including read-only files. Only the root folder itself can't be deleted or renamed.
- Be careful: `/data` holds this add-on's options (including passwords). Hide it with `hide_from_web: /data` if you want.

## Hiding files
`hide_from_web` hides **and blocks** entries in the web UI (listing, search, download, edit, rename, move, delete). One per line:
- full path: `/config/secrets.yaml`
- name matched anywhere: `.storage`
- wildcards: `*.db`, `/config/*.bak`, `/config/**/secrets*` (`*` = within a folder, `**` = across folders)

`hide_from_samba` hides names in Samba listings. Samba can only match names, not paths, so `secrets.yaml` is hidden in every share. Hidden items can still be opened if someone types the exact path. Restart the add-on after changing either list.

## Samba
- `smb://<ha-host>/files`. Create Samba users in the **Samba** tab. Each share card has copy-ready addresses.
- **macOS / iOS optimizations** (vfs_fruit) make Finder and the iOS Files app faster and keep Mac metadata. Turn it off if the shared folder is on a filesystem without extended attributes (e.g. FAT32/exFAT USB drives).
- If Samba can't bind, make sure nothing else (e.g. the Samba share add-on) uses port 445.
- `admin_password` is only needed if you open the UI directly on port 8080 (not exposed by default).

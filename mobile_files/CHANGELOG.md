# Changelog

## 2.3.0 - 2026-11-17

- **✨ AI help in the text editor** (Home Assistant add-on): **Explain it**, **Find mistakes** (syntax, outdated Home Assistant options, typos, passwords in plain text), **Change it** (describe the change; you see the new file before it goes in the editor, and nothing is saved until you tap Save) and **Ask**. Uses your Home Assistant AI Task (`ai_task_entity`, empty picks one). Not in the plain Docker version.

## 2.2.2 - 2026-11-17

- **Easier to read over wallpapers.** The page checks how bright the wallpaper is and tints behind the text just enough to keep it readable: bright wallpapers get more, dark ones barely any.

## 2.2.1 - 2026-11-17

- Uses the shared **add-on wallpaper** you pick in Photo Hub (new *Add-on wallpaper* tab). This add-on's own Wallpaper option still wins when set.

## 2.2.0 - 2026-11-16

- **Docker version: settings in the web UI.** New **Settings** tab (Docker only) to edit the hide-from-web and hide-from-Samba lists, the Trash folder and the Samba switch, saved in `/config/config.json`. The environment variables now only set the first-start values. The Home Assistant add-on keeps these on its Configuration tab.
- **Pick the web UI wallpaper.** New **Wallpaper** option on the Configuration tab. Leave it empty to use your Home Assistant theme's wallpaper, enter `none` for no wallpaper, or point it at your own image: drop it in `/config/www` and enter the file name (e.g. `wallpaper.jpg`), `/local/wallpaper.jpg`, or a full `https://` link.

## 2.1.0 - 2026-11-16

- **Follows your Home Assistant theme.** The web UI now uses the colors of whatever theme Home Assistant is showing, light or dark, and changes live when you switch. Themes with a dashboard wallpaper and frosted-glass cards (like Apple / Liquid Glass themes) carry over too: wallpaper behind, see-through blurred cards, matching corners. Opened outside Home Assistant it keeps its own look.

## 2.0.0
- **Docker version fixed**: the prebuilt Docker image now matches the add-on (select checkboxes hidden until select mode, new-file button, text editor, ingress-friendly paths). It's published as `ghcr.io/jonathangibson98/mobile-files-repo:latest`, and `docker-compose.yml` uses it.
- Docker now supports the same options as the add-on: `HIDE_FROM_WEB`, `HIDE_FROM_SAMBA`, `TRASH_DIR`.
- **Trash** with restore, delete forever, empty, and auto-empty after N days. Samba deletes go to the same Trash (recycle bin).
- **Search** the current folder recursively (wildcards supported).
- **Sort** by name/date/size/type, reverse, show/hide dot-files.
- **Favorites**: pin folders as chips, synced across devices; they follow renames and moves.
- **Copy**, **Duplicate**, **Copy to…** next to Move.
- **ZIP**: download folders/selections as ZIP, compress to ZIP, extract .zip/.tar(.gz/.xz/.bz2).
- **Viewer** for photos (swipe/arrows), video, audio and PDF.
- **Info** sheet: full path, size, file/folder count, permissions, owner.
- **Upload folders** and drag & drop upload on desktop, with progress and cancel.
- Long-press to select, Select all, bigger selection bar.
- Editor: save conflict warning, Ctrl/⌘+S, line-wrap toggle, unsaved-changes dot.
- Samba: copy-ready connect addresses per share, Browse button, optional macOS/iOS (vfs_fruit) optimizations.
- Sign out hidden under ingress; add to home screen support (web app manifest).

## 1.3.0
- Select mode (checkboxes hidden by default), new-file button, built-in text editor, hide lists, ingress.

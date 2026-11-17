# Changelog

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

# Mobile Files

Open it from the **Files** item in the Home Assistant sidebar (ingress, no extra login).

- Files are stored in HA's `/share` folder; the default Samba share `files` points there.
- Settings and the Samba user DB persist in the add-on's config folder.
- SMB: `smb://<ha-host>/files`. Create Samba users in the **Samba** tab.
- `admin_password` is only needed if you open the UI directly on port 8080 (ingress skips the login).
- Share folders must be inside `/share`. Add `/media` access by symlinking from `/share` if needed.

Note: if Samba says it can't bind, make sure nothing else (e.g. the Samba share add-on) is using port 445.

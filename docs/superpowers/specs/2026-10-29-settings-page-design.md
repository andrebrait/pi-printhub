# Settings page

Date: 2026-10-29. Extends [the main design](2026-10-21-pi-printhub-design.md) and [network sharing](2026-10-26-network-sharing-design.md).

## 1. Goal

The owner changes day-to-day settings from the web app instead of editing files over SSH and re-running the installer. A change takes effect without SSH, survives reinstalls, reboots and printer replugs, and the page cannot be used to run arbitrary commands as root.

There is one user: whoever has the web password.

## 2. Settings

| Group | Setting | Where it takes effect | Privilege |
|---|---|---|---|
| Storage | Keep documents for N days (`retention_days`, 1–365) | app and retention timer | app |
| Storage | Minimum free space in GB (`min_free_gb`, 0.5–1000) | app | app |
| Email | SMTP host, port (1–65535), security (`starttls` or `tls`), user, password, sender (`mail_from`) | app | app |
| Web password | current, new (at least 8 characters), repeat | app | app |
| Network sharing | Share the Pi's printer and scanner: the CUPS queue `printer-is-shared` and CUPS DNS-SD sharing, plus AirSane | CUPS, AirSane | root |
| Network sharing | Expose the printer directly (the "(USB)" entries): ipp-usb `interface = all` or `loopback` | ipp-usb | root |

Not managed by the page: `origin`, `data_dir`, `printer_queue` and CUPS job-detail visibility. They stay installer-time.

## 3. Storage

- `/etc/printhub/config.toml` keeps the installer's values and the keys the page does not manage.
- `/var/lib/printhub/settings.json` (owner `printhub`, mode 0600) holds values saved from the page. It may contain the app-level keys of section 2 plus `password_hash` and `session_secret`.
- `load_config()` reads `config.toml`, then overlays `settings.json` when it exists, and validates the merged result with the existing rules plus the ranges in section 2. A key in `settings.json` wins. The app and the retention timer both use `load_config()`, so they see the same values.
- The app writes `settings.json` atomically: a temporary file in the same directory with mode 0600, then rename. A failed validation writes nothing.

## 4. Applying app settings

- After a successful save the app replaces its current `Config`. Request handlers, the free-space check, mail sending and login read the current `Config` at use time, not a copy captured at startup.
- Changing the web password requires the current password. The new hash and a new `session_secret` are saved together, which invalidates every session; the response issues a fresh session cookie to the browser that made the change.
- The SMTP password is never returned by the API. `GET` reports whether one is set. A blank password field on save keeps the stored value.
- **Send test email** sends a short message to an address the user enters, with the values currently saved, and reports the SMTP error text on failure.

## 5. Applying root settings

### 5.1 Request and status files

- The app writes the wanted state to `/var/lib/printhub/system-request.json`: exactly `{"share": <bool>, "expose_usb": <bool>, "at": <ISO time>}`. `at` identifies the request.
- `printhub-apply.path` (`PathChanged=/var/lib/printhub/system-request.json`) starts `printhub-apply.service`, a root oneshot that runs `/usr/local/lib/printhub/apply-settings.sh`, installed next to `printer-setup.sh`. The installer installs the script and both units.
- The script writes `/var/lib/printhub/system-status.json` (owner `printhub`): `{"state": "applied" | "failed", "share": <bool>, "expose_usb": <bool>, "error": <string or null>, "at": <ISO time>, "request_at": <the request's at, or null>}`. `share` and `expose_usb` report the state actually in force after the run. The page treats a request as pending until a status carries its `at`.

### 5.2 Validation in the root script

The script refuses the request and writes a failed status when the file:

- is a symlink, is not a regular file, or is not owned by `printhub`;
- is larger than 1 KB;
- is not a JSON object with exactly the keys `share` and `expose_usb` (both booleans) and `at` (a string).

It parses with `python3 -c` and `json`, never with `eval` or shell interpolation of file content, and runs only fixed commands with no arguments taken from the request.

### 5.3 What each switch does

- `share` true: the queue is shared as today. For an IPP-over-USB queue, the apply step re-runs `printer-setup.sh`'s sharing step, which writes the AirPrint announcements in `/etc/avahi/services/printhub-*.service`, sets `printer-is-shared=true` and runs `cupsctl --share-printers BrowseLocalProtocols=none`. For the HPLIP fallback queue it sets `printer-is-shared=true` and runs `cupsctl --share-printers BrowseLocalProtocols=dnssd`. Then `systemctl enable --now airsaned`.
- `share` false: `lpadmin -p <queue> -o printer-is-shared=false`, `cupsctl --no-share-printers`, remove `/etc/avahi/services/printhub-airprint.service` and `printhub-generic.service`, `systemctl disable --now airsaned`.
- `expose_usb` false: an nftables rule drops new TCP connections from non-loopback interfaces to ipp-usb's HTTP port range (`http-min-port` to `http-max-port` in `ipp-usb.conf`, default 60000-65535). The script loads it atomically with `nft -f -` as table `inet printhub`, matching only `iifname != "lo" ct state new`, so replies to the Pi's own outgoing connections (the local ephemeral range overlaps) are never dropped. It takes effect immediately. `expose_usb` true: the table is removed. `printhub-firewall.service` re-applies the saved choice at boot, before the network is up.
- In both cases the script also sets `interface = all` or `loopback` in `/etc/ipp-usb/ipp-usb.conf`, without restarting ipp-usb: a restart can leave the printer's USB interface claimed until it is power-cycled, and ipp-usb has no reload. ipp-usb restarts by itself when the printer powers on or is replugged and when the Pi boots, so the "(USB)" DNS-SD adverts follow then. The settings path never restarts ipp-usb. The web app's own printing and scanning keep working either way, because they reach ipp-usb on localhost.
- If a command fails the script stops, reports the error, and reports the state it finds in force; the page shows that state.

### 5.4 Keeping choices across reinstalls and replugs

- `deploy/install.sh` and `deploy/printer-setup.sh` stop forcing sharing and `interface = all` unconditionally. They apply the switches from `system-request.json` through the same code as `apply-settings.sh`.
- With no request file the defaults are today's behaviour: shared and exposed.

## 6. Page and API

### 6.1 Navigation

A cog button (Material Symbols `settings`) next to the bell in the header links to `/settings.html`. It stays visible on phones. The collapsed menu also lists "Settings".

### 6.2 Page

One card per group, each with its own Save button and inline errors: Storage, Email (with **Send test email**), Web password, Network sharing. Each sharing switch shows "Applying…" until the status file reports the result, then "Applied" or the error.

### 6.3 API

All endpoints require a session; state-changing ones also pass the existing Origin check.

| Method and path | Purpose |
|---|---|
| `GET /api/settings` | Storage and email values; `smtp_password_set` instead of the password |
| `PUT /api/settings` | Save storage and email values; 400 with a message on invalid input |
| `POST /api/settings/password` | `{current, new}`; 403 on a wrong current password |
| `POST /api/settings/test-email` | `{to}`; reports the SMTP error on failure |
| `GET /api/settings/system` | Requested values plus the last status |
| `PUT /api/settings/system` | Write the request file |

### 6.4 Events

Each saved change adds an event-log entry naming what changed, for example "Retention changed from 30 to 14 days" or "Direct printer exposure turned off". Secrets never appear in events or logs.

## 7. Testing

- Python: merge and precedence of `settings.json` over `config.toml`; validation boundaries; atomic writes; password change invalidating other sessions; the SMTP password never returned; the API rejecting bad input; the request file written for the sharing switches. These use the existing fake-CUPS app fixture.
- Shell: `apply-settings.sh` tested like `tests/test_install.py`, with fake `systemctl`, `lpadmin` and `cupsctl` on `PATH`: refusing symlinks, wrong owners, oversized and malformed files; the commands and the `ipp-usb.conf` edit for each switch; the status file.
- On the Pi: toggle each switch and check `lpstat -v`, `cupsctl`, the nftables rule (`nft list table inet printhub`) and Bonjour from another machine; replug the printer and re-run the installer and confirm the choices stay; send a test email; change the password and confirm other sessions are logged out.

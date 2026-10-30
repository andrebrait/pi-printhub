<div align="center">

<img src="static/icons/logo.svg" alt="pi-printhub logo" width="128">

<h1>pi-printhub</h1>

<p>A Raspberry Pi print and scan server for a USB HP OfficeJet Pro 6970.</p>

</div>

It appears to every client on the LAN as a standard network printer and scanner. It also serves a web app at a hostname you choose, such as `https://print.example.com` (app listens on `127.0.0.1:8000`) that prints uploaded files and runs a scan, edit, export and email workflow.

- Hardware: Raspberry Pi with the HP OfficeJet Pro 6970 attached over USB
- Design spec: [`docs/superpowers/specs/2026-10-21-pi-printhub-design.md`](docs/superpowers/specs/2026-10-21-pi-printhub-design.md)
- Install: see [Install](#install)

Runtime dependencies come from Debian Trixie apt packages only. There is no pip or venv on the Pi.

## Install

On a fresh Raspberry Pi OS Lite 64-bit (Debian Trixie) with the printer plugged in and powered on:

```
git clone <this repo> && cd pi-printhub
sudo deploy/install.sh
```

The script installs packages, creates the CUPS queue, builds AirSane, installs Traefik, copies the app to `/opt/printhub`, writes `/etc/printhub/config.toml` and `/etc/traefik/traefik.env` (prompting for the web app hostname, web password, SMTP settings and Cloudflare token on first run; an existing config without the hostname gets it added on the next run) and starts the services. The ACME email is stored in `/etc/traefik/acme-email` (root-only, 0600) and substituted into Traefik's static configuration on every run. Without a printer attached, the install finishes and asks to be re-run once one is plugged in; updates reuse the existing queue without requiring the printer to be on.

### Network sharing

Any printer with an IPP-over-USB interface works; others fall back to HPLIP, which supports only the OfficeJet Pro 6970. Each time an IPP-over-USB printer is plugged in, `printhub-printer.service` points the `OfficeJet` queue, the scanner and the web app at it. The LAN then sees each device twice:

- `<model> (Pi)` and `<model> (Pi) (Generic)` (printers) and the AirSane scanner: CUPS and AirSane on the Pi accept the job or scan, render it, and talk to the printer themselves. Use these by default. Both printers are the same CUPS queue, announced by `/etc/avahi/services/printhub-*.service` instead of by CUPS (which would name it `<model> @ <host>`): `(Pi)` like a native AirPrint printer, which macOS sets up as AirPrint; `(Pi) (Generic)` the way CUPS announces it, which macOS sets up as a shared CUPS printer with Generic PostScript. Their location is the Pi's hostname, as for the AirSane scanner.
- `<printer name> (USB)`: ipp-usb passes the printer's own IPP and eSCL services through, with every native option.

The settings page can turn each of these off and back on; see [Settings](#settings).

Keep IPP enabled in the printer's own settings: on the OfficeJet Pro 6970 that switch also controls IPP-over-USB. Logs: `journalctl -u printhub-printer -u ipp-usb`. Design: [`docs/superpowers/specs/2026-10-26-network-sharing-design.md`](docs/superpowers/specs/2026-10-26-network-sharing-design.md).

Before the web app is reachable:

1. **Cloudflare token:** create one `CF_DNS_API_TOKEN` with both *Zone → Zone → Read* and *Zone → DNS → Edit*, scoped only to the zone of your hostname. Traefik/lego uses it to find the zone and complete Let's Encrypt DNS-01. Do not use a global API key.
2. **LAN DNS override:** point `print.example.com` at the Pi's LAN address in your router or local DNS. The installer prints the address at the end.
3. **DHCP reservation:** reserve that address for the Pi's MAC address (also printed), so the override stays valid.

Update: `git pull && sudo deploy/install.sh`. It skips steps that are already done, restarts `printhub`, `traefik` and `airsaned` and keeps existing configuration (a config from before the hostname setting asks for it once). Logs: `journalctl -u printhub -u traefik -u airsaned`.

Before a release, work through [`docs/hardware-checklist.md`](docs/hardware-checklist.md) on the Pi.

## Web app

Every page has a top bar with the printer's status (ready, printing, or its problems such as low ink or a paper jam) and a bell for notifications. The Jobs page lists every job on the queue, including jobs sent from phones and laptops, and cancels any of them. A background watcher in printhub records finished, failed and held jobs, printer problems, and failed scans or conversions in `data_dir/events.jsonl` (newest 500); the read state is shared by every device. The installer sets `JobPrivateValues none` in CUPS's default policy so the Jobs page can show job names and senders; anyone who can query the shared printer on the LAN can see them too.

Print shows Paper, Color, Sides, Copies and Pages; Layout and quality holds the other options. The crop dialog also works from the keyboard: arrow keys move the crop, Shift + arrow keys resize it.

### Settings

The cog in the header opens `/settings.html`. It changes:

- Storage: how many days documents are kept, and the minimum free space.
- Email: the SMTP server and sender. **Send test email** checks the values against a real address.
- Web password: changing it logs out every other session.
- Network sharing: whether the Pi's printer and scanner are shared on the network, and whether the printer is exposed directly (the `(USB)` entries).

App values are saved to `/var/lib/printhub/settings.json`, override `/etc/printhub/config.toml`, and apply immediately. Saved values survive reinstalls, reboots and printer replugs.

The two network switches are applied as root by `printhub-apply.path` and `printhub-apply.service`, which run `/usr/local/lib/printhub/apply-settings.sh`. The page shows Applying, then Applied or the error. With none saved, the printer is shared and exposed, as before.

With **Expose the printer directly** off, an nftables rule (`printhub-firewall.service` restores it at boot) drops new connections from other machines to ipp-usb's ports (60000 and up), so a LAN client's connection times out. The `(USB)` entries stop or start being advertised the next time the printer is powered on or the Pi restarts; ipp-usb is never restarted by the settings. If the printer stops answering over USB, power-cycle it.

Changing the SMTP server, port, user or security needs the SMTP password typed again; the other fields keep the stored one when it is left blank.

A forgotten web password: as root, delete `password_hash` and `session_secret` from `/var/lib/printhub/settings.json` and restart printhub. The password the installer wrote to `/etc/printhub/config.toml` applies again.


### Notifications

The settings page's Notifications card sends the Print Hub's events to any number of channels. Each channel has a name, an on/off switch, the event types it receives, a **Send test** button and its last result (`Delivered <time>` or `Failed: <reason>`).

- **Email**: one or more comma-separated addresses. **Use the scan email server** (on by default) sends through the SMTP settings of the scan email. Turn it off to give the channel its own server, port, security, user, password and sender.
- **Telegram**: the fields of Grafana's Telegram contact point: bot token, chat ID (a number or `@channelname`), optional message thread ID, parse mode, send silently, protect content and disable link preview.
- **Webhook**: a JSON POST to an `http` or `https` URL, with an optional header such as `Authorization`. The body has `id`, `at`, `level`, `kind`, `title`, `detail`, `job_id`, `doc_id`, `url` and `source` (`printhub`). It suits a self-hosted Apprise or ntfy. Redirects are not followed; any 2xx response counts as delivered.

Each channel takes any of five event types: **Print jobs**, **Scans**, **Printer status**, **File conversions** and **Settings changes**. All are on for a new channel.

Email and Telegram channels also have **Advanced options** in the channel form: a Telegram **header** (first line of every message) or an email **subject prefix**, and one message template per event type. A template may use `{title}`, `{detail}`, `{link}` and `{time}`; empty lines are dropped. Your text is sent as typed in the chosen Telegram parse mode (so `<b>{title}</b>` in HTML mode gives a bold title), while the values behind the placeholders are escaped for it. **Reset to defaults** restores the built-in text.

To silence an alert from the bell, choose **Silence** on it and pick "this alert" (same title) or "all" of its type, for 1 hour, 8 hours, 1 day or 1 week. A silence stops messages on every channel; the bell still records the event. The settings page lists active silences, and **Unsilence** ends one early.

Secrets (bot token, SMTP password, webhook header value) are stored in `/var/lib/printhub/notifications.json`, mode 0600, and are never shown again; leave a secret field blank on save to keep it. A failed send is retried after 1, 5 and 15 minutes, then dropped. Retries are kept in memory, so a restart drops them.

If the printer stops answering over USB for 2 minutes, a **Printer status** error "Printer not answering over USB" is sent, with a notice when it answers again. Power-cycle the printer if it lasts.

## Document safety

Cleanup skips documents used by conversions, scans, exports, downloads, email or print submission. Cross-process locks live under `data_dir/docs/.locks`; do not remove these files while either the app or retention timer is running. Metadata transactions are atomic and serialized. Interrupted scans recover on startup with completed pages intact and a Retry message.

Anonymous shares are admitted before body spooling: one at a time, bounded by the 5-file/200MiB pending quota and a maximum-body free-space reservation. They never trigger cleanup.

## Tests

```
python3 -m pytest -v
node --test static/js/*.test.js
```

## License

AGPL-3.0-only. See [LICENSE](LICENSE).

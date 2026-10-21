# pi-printhub

A Raspberry Pi print and scan server for a USB HP OfficeJet Pro 6970.

It appears to every client on the LAN as a standard network printer and scanner. It also serves a web app at `https://print.brait.dev` (app listens on `127.0.0.1:8000`) that prints uploaded files and runs a scan, edit, export and email workflow.

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

The script installs packages, creates the CUPS queue, builds AirSane, installs Traefik, copies the app to `/opt/printhub`, writes `/etc/printhub/config.toml` and `/etc/traefik/traefik.env` (prompting for the web password, SMTP settings and Cloudflare token on first run) and starts the services. The ACME email is stored in `/etc/traefik/acme-email` (root-only, 0600) and substituted into Traefik's static configuration on every run. An initial install aborts with "Printer not connected" if the OfficeJet Pro 6970 is not on USB; updates reuse the existing queue without requiring the printer to be on.

Before the web app is reachable:

1. **Cloudflare token:** create one `CF_DNS_API_TOKEN` with both *Zone → Zone → Read* and *Zone → DNS → Edit*, scoped only to `brait.dev`. Traefik/lego uses it to find the zone and complete Let's Encrypt DNS-01. Do not use a global API key.
2. **LAN DNS override:** point `print.brait.dev` at the Pi's LAN address in your router or local DNS. The installer prints the address at the end.
3. **DHCP reservation:** reserve that address for the Pi's MAC address (also printed), so the override stays valid.

Update: `git pull && sudo deploy/install.sh`. It skips steps that are already done, restarts `printhub`, `traefik` and `airsaned` and keeps existing configuration. Logs: `journalctl -u printhub -u traefik -u airsaned`.

Before a release, work through [`docs/hardware-checklist.md`](docs/hardware-checklist.md) on the Pi.

## Document safety

Cleanup skips documents used by conversions, scans, exports, downloads, email or print submission. Cross-process locks live under `data_dir/docs/.locks`; do not remove these files while either the app or retention timer is running. Metadata transactions are atomic and serialized. Interrupted scans recover on startup with completed pages intact and a Retry message.

Anonymous shares are admitted before body spooling: one at a time, bounded by the 5-file/200MiB pending quota and a maximum-body free-space reservation. They never trigger cleanup.

## Tests

```
python3 -m pytest -v
node --test static/js/
```

## License

AGPL-3.0-only. See [LICENSE](LICENSE).

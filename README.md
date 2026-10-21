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

The script installs packages, creates the CUPS queue, builds AirSane, installs Traefik, copies the app to `/opt/printhub`, writes `/etc/printhub/config.toml` and `/etc/traefik/traefik.env` (prompting for the web password, SMTP settings, Cloudflare token and ACME email on first run) and starts the services. It aborts with "Printer not connected" if the OfficeJet Pro 6970 is not on USB.

Before the web app is reachable:

1. **Cloudflare token:** create an API token with scope *Zone → DNS → Edit* on `brait.dev`. Traefik uses it for the Let's Encrypt DNS challenge.
2. **LAN DNS override:** point `print.brait.dev` at the Pi's LAN address in your router or local DNS. The installer prints the address at the end.
3. **DHCP reservation:** reserve that address for the Pi's MAC address (also printed), so the override stays valid.

Update: `git pull && sudo deploy/install.sh`. It skips steps that are already done, restarts `printhub` and `traefik` and keeps existing configuration. Logs: `journalctl -u printhub -u traefik -u airsaned`.

Before a release, work through [`docs/hardware-checklist.md`](docs/hardware-checklist.md) on the Pi.

## Tests

```
python3 -m pytest -v
node --test static/js/
```

## License

AGPL-3.0-only. See [LICENSE](LICENSE).

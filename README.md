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

The script installs packages, creates the CUPS queue, builds AirSane, installs Traefik, copies the app to `/opt/printhub`, writes `/etc/printhub/config.toml` and `/etc/traefik/traefik.env` (prompting for the web password, SMTP settings and Cloudflare token on first run) and starts the services. The ACME email is stored in `/etc/traefik/acme-email` (root-only, 0600) and substituted into Traefik's static configuration on every run. Without a printer attached, the install finishes and asks to be re-run once one is plugged in; updates reuse the existing queue without requiring the printer to be on.

### Network sharing

Any printer with an IPP-over-USB interface works; others fall back to HPLIP, which supports only the OfficeJet Pro 6970. Each time an IPP-over-USB printer is plugged in, `printhub-printer.service` points the `OfficeJet` queue, the scanner and the web app at it. The LAN then sees each device twice:

- `<model> @ raspberrypi` (printer) and the AirSane scanner: CUPS and AirSane on the Pi accept the job or scan, render it, and talk to the printer themselves. Use these by default.
- `<printer name> (USB)`: ipp-usb passes the printer's own IPP and eSCL services through, with every native option.

Keep IPP enabled in the printer's own settings: on the OfficeJet Pro 6970 that switch also controls IPP-over-USB. Logs: `journalctl -u printhub-printer -u ipp-usb`. Design: [`docs/superpowers/specs/2026-10-26-network-sharing-design.md`](docs/superpowers/specs/2026-10-26-network-sharing-design.md).

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
node --test static/js/*.test.js
```

## License

AGPL-3.0-only. See [LICENSE](LICENSE).

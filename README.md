# pi-printhub

A Raspberry Pi print and scan server for a USB HP OfficeJet Pro 6970.

It appears to every client on the LAN as a standard network printer and scanner. It also serves a web app at `https://print.brait.dev` (app listens on `127.0.0.1:8000`) that prints uploaded files and runs a scan, edit, export and email workflow.

- Hardware: Raspberry Pi with the HP OfficeJet Pro 6970 attached over USB
- Design spec: [`docs/superpowers/specs/2026-10-21-pi-printhub-design.md`](docs/superpowers/specs/2026-10-21-pi-printhub-design.md)
- Install: see `deploy/install.sh`

Runtime dependencies come from Debian Trixie apt packages only. There is no pip or venv on the Pi.

## Tests

```
python3 -m pytest -v
node --test static/js/
```

## License

AGPL-3.0-only. See [LICENSE](LICENSE).

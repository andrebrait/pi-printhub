# Network sharing: spooled queue plus native USB passthrough

Date: 2026-10-26. Extends section 3 of [the main design](2026-10-21-pi-printhub-design.md).

## 1. Goal

Network clients (macOS, iOS, Android, Windows) use the printer and scanner through the Pi only, see every feature the connected USB device supports, and keep working when a different IPP-over-USB printer is plugged in.

The owner disables the printer's own network printing and scanning services (section 6). The printer keeps its internet connection for HP Instant Ink.

## 2. Findings that shape the design

Observed on the Pi and the LAN on 2026-10-26:

- The printer accepts only `application/vnd.hp-PCL`, `application/PCLm`, `image/urf`, `image/pwg-raster` and `image/jpeg`. CUPS renders every job to `image/urf` on the Pi.
- The CUPS queue was created with `lpadmin -m everywhere`; its PPD names the model `Printer`. macOS then set the queue up with "Generic PostScript Printer", which has no duplex option. `driverless cat driverless:ipp://localhost:60000/ipp/print` (cups-filters) produces `*ModelName: "HP OfficeJet Pro 6970"` with `*OpenUI *Duplex`.
- The printer reports `printer-make-and-model = HP OfficeJet Pro 6970` and `printer-dns-sd-name = HP OfficeJet Pro 6970 [053F79]` over IPP-over-USB.
- AirSane advertises two scanners: `eSCL OfficeJet` (USB, via sane-airscan and ipp-usb) and `Hewlett-Packard officejet_pro_6970`, which is HPLIP's `hpaio:/net/...` device for the printer's own Ethernet interface.
- `ipp-usb` (0.9.23) runs with `interface = loopback`. With `interface = all` it advertises the device's own `_ipp._tcp`, `_uscan._tcp` and `_printer._tcp` services under the device's DNS-SD name plus ` (USB)`.
- Reported problems when clients use the printer's own network services: jobs stuck, the printer disappearing after the first job, half pages, and phones unable to cancel.

## 3. What clients see

| Entry | Path | Purpose |
|---|---|---|
| `<model> @ raspberrypi` printer (CUPS naming) | B: client → CUPS on the Pi → ipp-usb → USB | Default for everyday printing. CUPS accepts the whole job, renders it on the Pi, retries on printer errors, and answers cancel requests. |
| AirSane scanner on the Pi | B: client → AirSane → SANE → sane-airscan → ipp-usb → USB | Default for scanning. One scan at a time, driven by the Pi. |
| `<printer DNS-SD name> (USB)` printer and scanner | A: client → ipp-usb → USB | Every native capability, for features CUPS or AirSane do not pass through. The printer firmware handles these jobs directly. |

## 4. Design

### 4.1 A: ipp-usb on all interfaces

The installer sets `interface = all` in `/etc/ipp-usb/ipp-usb.conf` and restarts ipp-usb. ipp-usb advertises whichever IPP-over-USB device is attached; nothing else follows the device.

### 4.2 B: the CUPS queue follows the attached printer

- A udev rule matches a USB device with an IPP-over-USB interface (`ID_USB_INTERFACES` contains `:070104:`, as ipp-usb's own rule does) and starts `printhub-printer.service`. The installer runs the same service once.
- The service runs `deploy/printer-setup.sh`:
  1. Waits for ipp-usb to serve the device and reads the device's local port from `ipp-usb status` (the first device gets 60000; another device may not).
  2. Recreates the queue `OfficeJet` with `lpadmin -m driverless:ipp://localhost:<port>/ipp/print`, sets `printer-info` to the reported `printer-make-and-model`, shares it, and sets `printer-error-policy=retry-job`.
  3. Writes `/etc/sane.d/airscan.conf` for `http://localhost:<port>/eSCL` and the printhub `scanner.conf` drop-in, then restarts AirSane.
- The queue name stays `OfficeJet`, the web app's configured `printer_queue`; clients see `printer-info`.
- The installer no longer requires USB ID `03f0:0c54` for IPP-over-USB devices. The HPLIP fallback for devices without IPP-over-USB stays specific to the HP OfficeJet Pro 6970.

### 4.3 B: one AirSane scanner

AirSane ignores `hpaio:/net/*` devices, so it publishes only the USB scanner.

## 5. Out of scope

- Moving the printer to another VLAN or restricting it with firewall rules.
- Changing the printer's settings from the Pi; the owner does that through its web interface.
- More than one printer at a time: one queue and one scanner entry follow the most recently attached IPP-over-USB device.

## 6. Printer-side settings (owner)

In the printer's web interface (`https://10.0.254.0`), after the Pi entries are working:

- Disable: Bonjour/mDNS, AirPrint, IPP/IPPS printing, WS-Discovery, WS-Print, WSD Scan, Webscan/eSCL, SLP, LLMNR, LPD and port 9100 printing.
- Keep: network connection, DHCP/DNS, HP Web Services (Instant Ink).
- After each change, confirm from the Pi that `ipptool -t ipp://localhost:60000/ipp/print get-printer-attributes.test` and a web-app scan still work: some firmware applies the same switch to its USB interface.

## 7. Testing

- Installer tests: udev rule match, `ipp-usb.conf` rewrite, port parsing from `ipp-usb status`, and the generated `airscan.conf` and drop-in.
- On the Pi:
  - Replugging the printer rebuilds the queue, and `lpoptions -p OfficeJet` shows the real model.
  - A Mac added through Bonjour uses AirPrint and offers two-sided printing.
  - iOS and Android print two-sided through both entries.
  - A phone cancels a job mid-print on the CUPS entry.
  - Powering the printer off mid-job and back on completes the job on the CUPS entry.
  - Image Capture or Windows Scan scans through both scanner entries; the web app still scans.

# Hardware checklist

Run on the Pi with the OfficeJet Pro 6970 attached, before release.

- [ ] Every print option from the web app produces the expected output on paper (verify two-sided long edge and short edge physically)
- [ ] Printing works from an iPhone (AirPrint), a Mac, Android (default print service / Mopria) and Windows 11
- [ ] A flatbed scan and an ADF scan both work from the web app
- [ ] `sudo -u saned scanimage -L` lists the scanner
- [ ] The AirSane web UI at http://<pi>:8090 lists the scanner
- [ ] AirSane scans work from macOS Image Capture and Windows Scan
- [ ] The share target works from Android
- [ ] Unplugging the printer mid-job turns the top-bar status red, and the job resumes on replug
- [ ] A job sent from a phone appears on the Jobs page and can be cancelled there
- [ ] A paper jam or low ink appears in the notification center, and marking it read clears the count on a second device
- [ ] Replugging the printer runs `printhub-printer.service` successfully, and `lpoptions -p OfficeJet` shows the real model
- [ ] A Mac adding `<model> @ raspberrypi` through Bonjour uses AirPrint and offers two-sided printing
- [ ] iOS and Android print two-sided through both `<model> @ raspberrypi` and `<printer name> (USB)`
- [ ] A phone cancels a job mid-print on `<model> @ raspberrypi`
- [ ] Powering the printer off mid-job and back on completes the job on `<model> @ raspberrypi`
- [ ] Image Capture or Windows Scan scans through both the AirSane and the `(USB)` scanner; the web app still scans
- [ ] Confirm whether the ADF is single-sided
- [ ] Settings page: toggling sharing off removes `printer-is-shared=true` from `lpoptions -p OfficeJet`, deletes `/etc/avahi/services/printhub-*.service` and stops `airsaned`; toggling it on restores all three
- [ ] Settings page: with "Expose the printer directly" off, `curl -m5 http://<pi address>:60000/ipp/print` from another machine is dropped (curl times out), while the web app still prints and scans; the `(USB)` adverts change after a printer power cycle, and `systemctl status ipp-usb` shows no restart
- [ ] Reinstalling and replugging the printer keep the sharing and exposure choices

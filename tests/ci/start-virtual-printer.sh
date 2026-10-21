#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Start cupsd and an ippeveprinter-backed CUPS queue named TestPrinter.
# Needs the avahi-daemon and dbus packages: ippeveprinter exits without DNS-SD.
set -eu
here=$(cd "$(dirname "$0")" && pwd)

mkdir -p /run/dbus
dbus-daemon --system --fork
avahi-daemon --daemonize --no-chroot
cupsd
ippeveprinter -2 -k -c "$here/record-job.sh" -f application/pdf,image/pwg-raster -p 8631 TestPrinter >/tmp/ippeveprinter.log 2>&1 &

i=0
until (exec 3<>/dev/tcp/127.0.0.1/8631) 2>/dev/null; do
    i=$((i + 1))
    [ "$i" -lt 50 ] || { echo "ippeveprinter did not start" >&2; exit 1; }
    sleep 0.2
done

lpadmin -p TestPrinter -E -v ipp://localhost:8631/ipp/print -m everywhere

#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Points the CUPS queue, sane-airscan and printhub's scanner at the attached IPP-over-USB printer.
# Run by install.sh and, on plug, by printhub-printer.service.
set -euo pipefail

# ipp_usb_port STATE_DIR USB_SYSFS: the local port ipp-usb serves the attached device on.
# ipp-usb keeps one <vid>-<pid>-<serial>-<model>.state file per device it has seen, attached or not.
ipp_usb_port() {
    local state=$1 sysfs=$2 d file newest="" port
    for d in "$sysfs"/*; do
        [[ -r $d/idVendor && -r $d/idProduct ]] || continue
        for file in "$state/$(<"$d/idVendor")-$(<"$d/idProduct")-$(cat "$d/serial" 2>/dev/null)-"*.state; do
            [[ -e $file ]] || continue
            [[ -z $newest || $file -nt $newest ]] && newest=$file
        done
    done
    [[ -n $newest ]] || return 1
    port=$(sed -n 's/^http-port[[:space:]]*=[[:space:]]*\([0-9]\+\).*/\1/p' "$newest")
    [[ -n $port ]] || return 1
    echo "$port"
}

# airscan_conf PORT MODEL: a sane-airscan configuration with the device as its only, first (e0) entry.
airscan_conf() {
    local name=${2//\\/\\\\}
    name=${name//\"/\\\"}
    printf '[devices]\n"%s" = http://localhost:%s/eSCL\n\n[options]\ndiscovery = disable\n' "$name" "$1"
}

# scanner_device MODEL: the SANE name sane-airscan gives the airscan_conf entry.
scanner_device() { echo "airscan:e0:$1"; }

# pin_scanner DROPIN_DIR [DEVICE]: make printhub use DEVICE instead of full SANE discovery (about 10 s per lookup);
# without DEVICE, drop the pin so printhub discovers the scanner itself.
pin_scanner() {
    local value
    if [[ -n ${2:-} ]]; then
        value=${2//\\/\\\\}
        value=${value//\"/\\\"}
        value=${value//%/%%}
        install -d -m 0755 "$1"
        printf '[Service]\nEnvironment="PRINTHUB_SCANNER=%s"\n' "$value" >"$1/scanner.conf"
    else
        rm -f "$1/scanner.conf"
    fi
}

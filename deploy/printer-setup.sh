#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Points the CUPS queue, sane-airscan and printhub's scanner at the attached IPP-over-USB printer.
# Run by install.sh and, on plug, by printhub-printer.service.
set -euo pipefail

# ipp_usb_port STATE_DIR USB_SYSFS: the local port ipp-usb serves the attached device on.
# ipp-usb keeps one <vid>-<pid>[-<serial>]-<model>.state file per device it has seen, attached or not; it maps
# serial characters outside [0-9A-Za-z_-] to "-". One printer at a time: the first attached match wins.
ipp_usb_port() {
    local state=$1 sysfs=$2 d serial file port
    for d in "$sysfs"/*; do
        [[ -r $d/idVendor && -r $d/idProduct ]] || continue
        serial=$(cat "$d/serial" 2>/dev/null) || serial=""
        serial=${serial//[^0-9A-Za-z_-]/-}
        for file in "$state/$(<"$d/idVendor")-$(<"$d/idProduct")-${serial:+$serial-}"*.state; do
            [[ -e $file ]] || continue
            port=$(sed -n 's/^http-port[[:space:]]*=[[:space:]]*\([0-9]\+\).*/\1/p' "$file")
            [[ -n $port ]] && { echo "$port"; return 0; }
        done
    done
    return 1
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

# airprint_service MODEL QUEUE [generic]: an avahi service file announcing the CUPS queue, from `ipptool -tv`
# attributes of the queue on stdin.
# - "<model> (Pi)" is shaped like a printer's own AirPrint announcement; macOS sets it up as AirPrint.
# - "<model> (Pi) (Generic)" is what CUPS itself announces (PostScript, printer-type); macOS lists that as "Bonjour
#   Shared" and picks a driver, usually Generic PostScript. It replaces CUPS's own "<model> @ <host>" as a fallback.
# No UUID: clients that merge entries by UUID would show the two as one.
airprint_service() {
    local attrs name value pdl="" f formats urf color duplex txt="" type generic=${3:-}
    attrs=$(cat)
    value() { sed -n "s/^[[:space:]]*$1 ([^)]*) = //p" <<<"$attrs" | head -n1; }
    formats="application/pdf image/jpeg image/png image/pwg-raster image/urf"
    [[ -n $generic ]] && formats="application/pdf application/postscript image/jpeg image/png image/pwg-raster image/urf"
    for f in $formats; do
        [[ ,$(value document-format-supported), == *,$f,* ]] && pdl+=${pdl:+,}$f
    done
    urf=$(value urf-supported)
    [[ $(value color-supported) == true ]] && color=T || color=F
    [[ $(value sides-supported) == *two-sided* ]] && duplex=T || duplex=F
    # \& : a bare & in the replacement stands for the matched text (bash 5.2 patsub_replacement).
    name=${1//&/\&amp;}
    name=${name//</\&lt;}
    name=${name//>/\&gt;}
    local -a records=(txtvers=1 qtotal=1 "rp=printers/$2" "ty=$name" "product=($name)" "pdl=$pdl" "URF=${urf:-none}"
        "Color=$color" "Duplex=$duplex")
    value=$(value printer-location)
    value=${value//&/\&amp;}
    value=${value//</\&lt;}
    [[ -n $value ]] && records+=("note=${value//>/\&gt;}")
    if [[ -n $generic ]]; then
        value=$(value printer-type)
        [[ -n $value ]] && records+=("printer-type=$(printf '0x%X' "$value")")
    else
        records+=(air=none)
    fi
    for value in "${records[@]}"; do
        txt+="    <txt-record>$value</txt-record>"$'\n'
    done
    printf '<?xml version="1.0" standalone="no"?>\n<!DOCTYPE service-group SYSTEM "avahi-service.dtd">\n'
    printf '<service-group>\n  <name>%s (Pi)%s</name>\n' "$name" "${generic:+ (Generic)}"
    for type in _ipp _ipps; do
        printf '  <service>\n    <type>%s._tcp</type>\n' "$type"
        [[ $type == _ipp ]] && printf '    <subtype>_universal._sub._ipp._tcp</subtype>\n'
        printf '    <port>631</port>\n%s  </service>\n' "$txt"
    done
    printf '</service-group>\n'
}

# apply_printer: point the queue, sane-airscan and printhub at the attached device. Changes nothing unless the
# device answers IPP: the previous queue keeps its jobs and settings.
apply_printer() {
    local state=${STATE_DIR:-/var/ipp-usb/dev} sysfs=${USB_SYSFS:-/sys/bus/usb/devices}
    local sane=${SANE_DIR:-/etc/sane.d} dropin=${DROPIN_DIR:-/etc/systemd/system/printhub.service.d}
    local avahi=${AVAHI_DIR:-/etc/avahi/services}
    local queue=${PRINTER_QUEUE:-OfficeJet} port="" uri="" model="" i tmp attrs kind
    # ipp-usb writes the state file before it serves the device (and keeps it from earlier plugs), so wait until
    # the printer itself answers.
    for ((i = 0; i < 30; i++)); do
        if port=$(ipp_usb_port "$state" "$sysfs"); then
            uri=ipp://localhost:$port/ipp/print
            model=$(ipptool -tv "$uri" get-printer-attributes.test |
                sed -n 's/^[[:space:]]*printer-make-and-model ([^)]*) = //p' | head -n1) || true
            [[ -n $model ]] && break
        fi
        sleep 1
    done
    [[ -n $model ]] || { echo "no IPP-over-USB printer answered IPP${uri:+ at $uri}" >&2; return 1; }

    # Location: the hostname, which AirSane's scanner also shows.
    lpadmin -p "$queue" -E -v "$uri" -m "driverless:$uri" -D "$model" -L "$(hostname)" \
        -o printer-is-shared=true -o printer-error-policy=retry-job
    install -d -m 0755 "$avahi"
    attrs=$(ipptool -tv "ipp://localhost:631/printers/$queue" get-printer-attributes.test) || true
    for kind in airprint generic; do
        tmp=$(mktemp "$avahi/.printhub-$kind.XXXXXX")
        airprint_service "$model" "$queue" "${kind#airprint}" <<<"$attrs" >"$tmp"
        chmod 0644 "$tmp" # avahi-daemon reads it
        mv "$tmp" "$avahi/printhub-$kind.service"
    done
    # Shared, but announced by the files above instead of by CUPS, which always names it "<info> @ <host>".
    # After reading the queue: cupsctl restarts cupsd.
    cupsctl --share-printers BrowseLocalProtocols=none
    tmp=$(mktemp "$sane/airscan.conf.XXXXXX")
    airscan_conf "$port" "$model" >"$tmp"
    chmod 0644 "$tmp" # saned and printhub read it
    mv "$tmp" "$sane/airscan.conf"
    pin_scanner "$dropin" "$(scanner_device "$model")"
    systemctl daemon-reload
    # Missing on a first install, before install.sh installs them; it starts them itself.
    systemctl try-restart printhub airsaned || true
}

# Sourcing the file (for install.sh and tests) must not change anything.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    apply_printer
fi

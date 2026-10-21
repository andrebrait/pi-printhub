#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Installs pi-printhub on Raspberry Pi OS Lite 64-bit (Debian Trixie). Safe to re-run.
set -euo pipefail

AIRSANE_TAG=v0.4.15
TRAEFIK_VERSION=v3.7.16
PRINTER_QUEUE=OfficeJet
USB_SYSFS=${USB_SYSFS:-/sys/bus/usb/devices}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

die() { echo "error: $*" >&2; exit 1; }
log() { echo "==> $*"; }

# Prints the sysfs directory of the HP OfficeJet Pro 6970 (03f0:0c54), if attached.
printer_dev() {
    local d
    for d in "$USB_SYSFS"/*; do
        [[ -r $d/idVendor && -r $d/idProduct ]] || continue
        if [[ $(<"$d/idVendor") == 03f0 && $(<"$d/idProduct") == 0c54 ]]; then
            echo "$d"
            return 0
        fi
    done
    return 1
}

# True when the printer exposes an IPP-over-USB interface (class 07, subclass 01, protocol 04).
has_ipp_usb() {
    local dev i
    dev=$(printer_dev) || return 1
    for i in "$dev"/*:*; do
        [[ -r $i/bInterfaceClass ]] || continue
        if [[ $(<"$i/bInterfaceClass") == 07 && $(<"$i/bInterfaceSubClass") == 01 && $(<"$i/bInterfaceProtocol") == 04 ]]; then
            return 0
        fi
    done
    return 1
}

ask() { # ask VAR "prompt" [default]
    local -n out=$1
    read -r -p "$2${3:+ [$3]}: " out
    out=${out:-${3:-}}
    [[ -n $out ]] || die "$2 must not be empty"
}

ask_secret() { # ask_secret VAR "prompt"
    local -n out=$1
    read -r -s -p "$2: " out
    echo
    [[ -n $out ]] || die "$2 must not be empty"
}

install_packages() {
    log "Installing packages"
    apt-get update
    apt-get install -y curl cups avahi-daemon sane-utils sane-airscan libsane-dev libjpeg-dev \
        libpng-dev libavahi-client-dev libusb-1.0-0-dev cmake g++ git python3-fastapi \
        python3-uvicorn python3-python-multipart python3-argon2 python3-itsdangerous \
        python3-img2pdf python3-pil python3-magic python3-cups python3-pikepdf \
        libjpeg-turbo-progs poppler-utils libheif-examples libreoffice-nogui
}

setup_printer() {
    local dev uri ppd
    dev=$(printer_dev) || die "Printer not connected (no USB device 03f0:0c54)"
    if has_ipp_usb; then
        log "Printer speaks IPP-over-USB"
        apt-get install -y ipp-usb
        lpstat -p "$PRINTER_QUEUE" &>/dev/null ||
            lpadmin -p "$PRINTER_QUEUE" -E -v ipp://localhost:60000/ipp/print -m everywhere
        install -m 0644 "$REPO/deploy/sane/airscan.conf" /etc/sane.d/airscan.conf
    else
        log "Printer has no IPP-over-USB interface; using HPLIP"
        apt-get install -y hplip printer-driver-hpcups libsane-hpaio
        if ! lpstat -p "$PRINTER_QUEUE" &>/dev/null; then
            uri=$(hp-makeuri -c "$(printf '%03d:%03d' "$(<"$dev/busnum")" "$(<"$dev/devnum")")" | grep -om1 'hp:/[^ ]*') ||
                die "hp-makeuri found no URI for the printer"
            ppd=drv:///hpcups.drv/hp-officejet_pro_6970.ppd
            lpinfo -m | grep -F "$ppd" >/dev/null ||
                ppd=$(lpinfo -m | grep -i -m1 'officejet pro 6970' | cut -d' ' -f1)
            [[ -n $ppd ]] || die "no CUPS driver found for the OfficeJet Pro 6970"
            lpadmin -p "$PRINTER_QUEUE" -E -v "$uri" -m "$ppd"
        fi
        # No eSCL endpoint to point sane-airscan at; hpaio drives the scanner.
        sed '/^"OfficeJet"/d' "$REPO/deploy/sane/airscan.conf" >/etc/sane.d/airscan.conf
    fi
    # AirSane finds the scanner through sane-airscan; a second, discovered eSCL copy would duplicate it.
    sed -i 's/^escl\b/#escl/' /etc/sane.d/dll.conf
    cupsctl --share-printers
    lpadmin -p "$PRINTER_QUEUE" -o printer-is-shared=true
}

install_airsane() {
    local src=/usr/local/src/AirSane
    if [[ ! -x /usr/local/bin/airsaned || ! -e $src/.installed-$AIRSANE_TAG ]]; then
        log "Building AirSane $AIRSANE_TAG"
        [[ -d $src/.git ]] || git clone https://github.com/SimulPiscator/AirSane "$src"
        git -C "$src" fetch --tags
        git -C "$src" checkout -q "$AIRSANE_TAG"
        mkdir -p "$src/build"
        (cd "$src/build" && cmake .. && make -j"$(nproc)" && make install)
        touch "$src/.installed-$AIRSANE_TAG"
    fi
    # With ipp-usb the scanner reaches SANE through sane-airscan, which AirSane sees as a network scanner.
    sed -i 's/^LOCAL_SCANNERS_ONLY=.*/LOCAL_SCANNERS_ONLY=false/' /etc/default/airsane
}

install_traefik() {
    local tgz=traefik_${TRAEFIK_VERSION}_linux_arm64.tar.gz
    local base=https://github.com/traefik/traefik/releases/download/$TRAEFIK_VERSION tmp
    if ! /usr/local/bin/traefik version 2>/dev/null | grep -qw "${TRAEFIK_VERSION#v}"; then
        log "Installing Traefik $TRAEFIK_VERSION"
        tmp=$(mktemp -d)
        curl -fsSL -o "$tmp/$tgz" "$base/$tgz"
        curl -fsSL -o "$tmp/checksums.txt" "$base/traefik_${TRAEFIK_VERSION}_checksums.txt"
        (cd "$tmp" && grep -F "  $tgz" checksums.txt | sha256sum -c -)
        tar -xzf "$tmp/$tgz" -C "$tmp" traefik
        install -m 0755 "$tmp/traefik" /usr/local/bin/traefik
        rm -rf "$tmp"
    fi
    install -d -m 0755 /etc/traefik
    install -m 0644 "$REPO"/deploy/traefik/*.yml /etc/traefik/
}

install_app() {
    getent passwd printhub >/dev/null ||
        useradd --system --home-dir /var/lib/printhub --no-create-home --shell /usr/sbin/nologin printhub
    usermod -aG lp,lpadmin,scanner printhub
    getent passwd traefik >/dev/null ||
        useradd --system --home-dir /var/lib/traefik --no-create-home --shell /usr/sbin/nologin traefik
    install -d -o printhub -g printhub -m 0750 /var/lib/printhub /var/lib/printhub/tmp
    install -d -o traefik -g traefik -m 0700 /var/lib/traefik
    install -d -m 0755 /opt/printhub
    rm -rf /opt/printhub/printhub /opt/printhub/static
    cp -r "$REPO/printhub" "$REPO/static" /opt/printhub/
    find /opt/printhub -name __pycache__ -prune -exec rm -rf {} +
}

write_config() {
    local cfg=/etc/printhub/config.toml pw pw2 hash host port security user smtp_pw from
    [[ ! -e $cfg ]] || return 0
    ask_secret pw "Web password"
    ask_secret pw2 "Repeat web password"
    [[ $pw == "$pw2" ]] || die "passwords differ"
    # Hash from the checkout; the password goes through stdin, never argv.
    hash=$(printf %s "$pw" | PYTHONPATH=$REPO python3 -c \
        'import sys; from printhub.auth import hash_password; print(hash_password(sys.stdin.read()))')
    ask host "SMTP host"
    ask port "SMTP port" 587
    ask security "SMTP security (starttls or tls)" starttls
    [[ $security == starttls || $security == tls ]] || die "SMTP security must be starttls or tls"
    ask user "SMTP user"
    ask_secret smtp_pw "SMTP password"
    ask from "Mail From address" "$user"

    install -d -m 0750 -o root -g printhub /etc/printhub
    (
        umask 077
        PASSWORD_HASH=$hash SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))') \
            SMTP_HOST=$host SMTP_PORT=$port SMTP_SECURITY=$security SMTP_USER=$user \
            SMTP_PASSWORD=$smtp_pw MAIL_FROM=$from python3 -c '
import json, os
for k in ("password_hash", "session_secret", "smtp_host", "smtp_security", "smtp_user", "smtp_password", "mail_from"):
    print(k, "=", json.dumps(os.environ[k.upper()]))
print("smtp_port =", int(os.environ["SMTP_PORT"]))' >"$cfg"
    )
    chown printhub:printhub "$cfg"
}

write_traefik_env() {
    local env=/etc/traefik/traefik.env token email
    [[ ! -e $env ]] || return 0
    ask_secret token "Cloudflare API token (Zone > DNS > Edit on brait.dev)"
    ask email "ACME account email"
    (
        umask 077
        printf 'CF_DNS_API_TOKEN=%s\nTRAEFIK_CERTIFICATESRESOLVERS_LE_ACME_EMAIL=%s\n' "$token" "$email" >"$env"
    )
}

start_services() {
    install -m 0644 "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.timer /etc/systemd/system/
    systemctl daemon-reload
    # Pick up new code and config on re-runs; enable --now then starts whatever was not running.
    systemctl try-restart printhub traefik
    systemctl enable --now printhub traefik airsaned printhub-retention.timer
}

print_network_todo() {
    local dev addr mac
    read -r dev addr < <(ip -4 -o route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) { if ($i == "dev") d = $(i + 1); if ($i == "src") s = $(i + 1) } print d, s}')
    mac=$(<"/sys/class/net/$dev/address")
    cat <<MSG

Done. Two things remain on your router/DNS:
  1. LAN DNS override: print.brait.dev -> $addr
  2. DHCP reservation: $mac -> $addr
MSG
}

main() {
    [[ $EUID -eq 0 ]] || die "run as root (sudo deploy/install.sh)"
    install_packages
    setup_printer
    install_airsane
    install_traefik
    install_app
    write_config
    write_traefik_env
    start_services
    print_network_todo
}

# Sourcing the file (for tests) must not install anything.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi

#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Installs pi-printhub on Raspberry Pi OS Lite 64-bit (Debian Trixie). Safe to re-run.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1 # root python runs must not leave __pycache__ in the checkout

AIRSANE_TAG=v0.4.15
TRAEFIK_VERSION=v3.7.16
PRINTER_QUEUE=OfficeJet
USB_SYSFS=${USB_SYSFS:-/sys/bus/usb/devices}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=deploy/printer-setup.sh
source "$REPO/deploy/printer-setup.sh"

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

# Prints the sysfs directory of the first attached device with an IPP-over-USB interface (class 07, subclass 01,
# protocol 04), whatever its make.
ipp_usb_dev() {
    local d i
    for d in "$USB_SYSFS"/*; do
        for i in "$d"/*:*; do
            [[ -r $i/bInterfaceClass ]] || continue
            if [[ $(<"$i/bInterfaceClass") == 07 && $(<"$i/bInterfaceSubClass") == 01 && $(<"$i/bInterfaceProtocol") == 04 ]]; then
                echo "$d"
                return 0
            fi
        done
    done
    return 1
}

# ipp_usb_listen_all FILE: ipp-usb publishes the device's own IPP and eSCL services on the LAN, not only to the Pi.
ipp_usb_listen_all() { sed -i 's/^\([[:space:]]*interface[[:space:]]*=[[:space:]]*\)loopback/\1all/' "$1"; }

# airsane_ignore_hpaio_net FILE: AirSane must not republish HPLIP's view of a printer's own network scanner.
airsane_ignore_hpaio_net() { grep -qxF 'hpaio:/net/.*' "$1" || echo 'hpaio:/net/.*' >>"$1"; }

# show_job_details CUPSD_CONF: let the Jobs page see job names and senders (CUPS hides them by default), in the
# default policy only. Succeeds when it changed the file, so the caller restarts CUPS only then.
show_job_details() {
    grep -q '^<Policy default>' "$1" || return 1
    local policy
    policy=$(sed -n '/^<Policy default>/,/^<\/Policy>/p' "$1")
    grep -q '^[[:space:]]*JobPrivateValues none$' <<<"$policy" && return 1
    if grep -q '^[[:space:]]*JobPrivateValues ' <<<"$policy"; then
        sed -i '/^<Policy default>/,/^<\/Policy>/ s/^\([[:space:]]*JobPrivateValues\) .*/\1 none/' "$1"
    else
        sed -i '/^<Policy default>/a\  JobPrivateValues none' "$1"
    fi
}

valid_port() { [[ $1 =~ ^[0-9]{1,5}$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535)); }
valid_security() { [[ $1 == starttls || $1 == tls ]]; }
valid_hostname() { [[ ${#1} -le 253 && $1 =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,63}|xn--[a-z0-9-]{1,59})$ ]]; }

# ask_host: WEB_HOST from the user, lowercased (local -l) because browsers send the Origin host in lowercase.
ask_host() {
    local -l host
    ask host "Web app hostname (for example print.example.com)" "" valid_hostname
    WEB_HOST=$host
}

# config_host CONFIG: the host of the config's origin, an empty line when it has none; fails on an unusable origin.
config_host() {
    python3 -c 'import sys, tomllib, urllib.parse
cfg = tomllib.load(open(sys.argv[1], "rb"))
if "origin" in cfg:
    o = cfg["origin"]
    host = isinstance(o, str) and urllib.parse.urlsplit(o).hostname
    if not host:
        sys.exit(f"error: origin {o!r} in {sys.argv[1]} is not a URL")
    print(host)
else:
    print()' "$1"
}

# add_origin CONFIG HOST: the config with origin first, where TOML keeps it at the top level.
add_origin() { printf 'origin = "https://%s"\n' "$2"; cat "$1"; }

ask() { # ask VAR "prompt" [default] [validator]
    local -n out=$1
    local validate=${4:-true}
    while :; do
        read -r -p "$2${3:+ [$3]}: " out
        out=${out:-${3:-}}
        [[ -n $out ]] || die "$2 must not be empty"
        "$validate" "$out" && return 0
        echo "invalid value: $out" >&2
    done
}

ask_secret() { # ask_secret VAR "prompt"
    local -n out=$1
    read -r -s -p "$2: " out
    echo
    [[ -n $out ]] || die "$2 must not be empty"
}

# write_secret FILE OWNER: stdin to FILE, mode 0600, moved into place only once complete.
write_secret() {
    local file=$1 owner=$2 tmp
    tmp=$(mktemp "$file.XXXXXX")
    if cat >"$tmp" && chown "$owner" "$tmp" && chmod 600 "$tmp" && mv "$tmp" "$file"; then
        return 0
    fi
    rm -f "$tmp"
    return 1
}

# pick_ppd LPINFO_M_OUTPUT: the OfficeJet Pro 6970 driver, or nothing.
pick_ppd() {
    local ppd=drv:///hpcups.drv/hp-officejet_pro_6970.ppd line
    if grep -qF "$ppd" <<<"$1"; then
        echo "$ppd"
        return 0
    fi
    line=$(grep -i -m1 'officejet pro 6970' <<<"$1" || true)
    echo "${line%% *}"
}

# Enable sane-airscan and disable the eSCL backend (a second, discovered copy would duplicate the scanner).
configure_dll_conf() {
    sed -i 's/^escl\b/#escl/; s/^#[[:space:]]*airscan\b/airscan/' "$1"
    grep -q '^airscan\b' "$1" || echo airscan >>"$1"
}

# AirSane's stock ignore list hides airscan:* devices, which is how it reaches this scanner.
allow_airscan_in_airsane() { sed -i '/^airscan:/d' "$1"; }

install_packages() {
    log "Installing packages"
    apt-get update
    apt-get install -y curl cups avahi-daemon sane-utils sane-airscan libsane-dev libjpeg-dev \
        libpng-dev libavahi-client-dev libusb-1.0-0-dev cmake g++ git python3-fastapi \
        python3-uvicorn python3-python-multipart python3-argon2 python3-itsdangerous \
        python3-img2pdf python3-pil python3-magic python3-cups \
        libjpeg-turbo-progs poppler-utils libheif-examples libreoffice-nogui
}

# The queue, sane-airscan and printhub follow whichever IPP-over-USB printer is attached: printhub-printer.service
# runs printer-setup.sh now and, through the udev rule, on every plug. ipp-usb also publishes the device natively.
# setup_ipp_usb QUEUE_URI ATTACHED: the existing queue's device line (empty without a queue), and 1 when an
# IPP-over-USB printer is attached. Detected before the restarts below, which briefly hide its USB interfaces.
setup_ipp_usb() {
    apt-get install -y ipp-usb
    ipp_usb_listen_all /etc/ipp-usb/ipp-usb.conf
    install -D -m 0755 "$REPO/deploy/printer-setup.sh" /usr/local/lib/printhub/printer-setup.sh
    install -m 0644 "$REPO/deploy/systemd/printhub-printer.service" /etc/systemd/system/printhub-printer.service
    install -D -m 0644 "$REPO/deploy/udev/70-printhub-printer.rules" /etc/udev/rules.d/70-printhub-printer.rules
    systemctl daemon-reload
    udevadm control --reload
    systemctl try-restart ipp-usb
    # ipp-usb is started by udev on plug; the printer may have been plugged in before ipp-usb was installed.
    udevadm trigger --action=add --subsystem-match=usb
    if ((!$2)); then
        log "Printer unplugged; keeping the existing queue. It is set up again when plugged in."
    # The unit, not a direct call: it serializes with the run udev just triggered and keeps set -e intact.
    elif ! systemctl start printhub-printer.service; then
        [[ -n $1 ]] || PRINTER_PENDING=1
        log "warning: the printer did not answer over IPP-over-USB (is IPP enabled in its settings?); queue unchanged. Details: journalctl -u printhub-printer. Re-run this script after fixing it."
    fi
}

create_hplip_queue() { # create_hplip_queue USB_SYSFS_DIR
    local uri ppd models
    apt-get install -y hplip printer-driver-hpcups libsane-hpaio
    uri=$(hp-makeuri -c "$(printf '%03d:%03d' "$(<"$1/busnum")" "$(<"$1/devnum")")" | grep -om1 'hp:/[^ ]*') ||
        die "hp-makeuri found no URI for the printer"
    models=$(lpinfo -m)
    ppd=$(pick_ppd "$models")
    [[ -n $ppd ]] || die "no CUPS driver found for the OfficeJet Pro 6970"
    lpadmin -p "$PRINTER_QUEUE" -E -v "$uri" -m "$ppd"
}

setup_printer() {
    local sane=${SANE_DIR:-/etc/sane.d} dropin=${DROPIN_DIR:-/etc/systemd/system/printhub.service.d}
    local avahi=${AVAHI_DIR:-/etc/avahi/services} dev uri=""
    if lpstat -p "$PRINTER_QUEUE" &>/dev/null; then
        uri=$(lpstat -v "$PRINTER_QUEUE")
    fi
    if ipp_usb_dev >/dev/null; then
        log "Printer speaks IPP-over-USB"
        setup_ipp_usb "$uri" 1
    elif [[ $uri == *//localhost:6????/* ]]; then
        log "IPP-over-USB queue; printer not attached"
        setup_ipp_usb "$uri" 0
    elif [[ -n $uri ]] || dev=$(printer_dev); then
        if [[ -z $uri ]]; then
            log "Printer has no IPP-over-USB interface; using HPLIP"
            create_hplip_queue "$dev"
        fi
        # No eSCL endpoint to point sane-airscan at; hpaio drives the scanner.
        printf '[options]\ndiscovery = disable\n' >"$sane/airscan.conf"
        pin_scanner "$dropin"
        # printer-setup.sh's announcements belong to an ipp-usb queue; CUPS announces this one itself.
        rm -f "$avahi/printhub-airprint.service" "$avahi/printhub-generic.service"
        cupsctl --share-printers BrowseLocalProtocols=dnssd
        lpadmin -p "$PRINTER_QUEUE" -o printer-is-shared=true
    else
        PRINTER_PENDING=1
        log "No printer connected; skipping the print queue and scanner setup. Re-run this script after plugging it in."
        return 0
    fi
    configure_dll_conf "$sane/dll.conf"
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
    allow_airscan_in_airsane /etc/airsane/ignore.conf
    airsane_ignore_hpaio_net /etc/airsane/ignore.conf
}

install_traefik() {
    local tgz=traefik_${TRAEFIK_VERSION}_linux_arm64.tar.gz
    local base=https://github.com/traefik/traefik/releases/download/$TRAEFIK_VERSION
    if ! /usr/local/bin/traefik version 2>/dev/null | grep -qw "${TRAEFIK_VERSION#v}"; then
        log "Installing Traefik $TRAEFIK_VERSION"
        TMPD=$(mktemp -d)
        trap 'rm -rf "$TMPD"' EXIT
        curl -fsSL -o "$TMPD/$tgz" "$base/$tgz"
        curl -fsSL -o "$TMPD/checksums.txt" "$base/traefik_${TRAEFIK_VERSION}_checksums.txt"
        (cd "$TMPD" && grep -F "  $tgz" checksums.txt | sha256sum -c -)
        tar -xzf "$TMPD/$tgz" -C "$TMPD" traefik
        install -m 0755 "$TMPD/traefik" /usr/local/bin/traefik
    fi
    install -d -m 0755 /etc/traefik
}

# render_traefik_yml EMAIL: the static config with the ACME email filled in.
render_traefik_yml() {
    ACME_EMAIL=$1 python3 -c '
import json, os, pathlib, sys
sys.stdout.write(pathlib.Path(sys.argv[1]).read_text().replace(
    "\"@ACME_EMAIL@\"", json.dumps(os.environ["ACME_EMAIL"])))' "$REPO/deploy/traefik/traefik.yml"
}

# render_dynamic_yml HOST: the router config for the web app's hostname (valid_hostname already passed).
render_dynamic_yml() { sed "s/@WEB_HOST@/$1/" "$REPO/deploy/traefik/dynamic.yml"; }

install_traefik_config() {
    render_traefik_yml "$(</etc/traefik/acme-email)" >/etc/traefik/traefik.yml
    chmod 0644 /etc/traefik/traefik.yml
    render_dynamic_yml "$WEB_HOST" >/etc/traefik/dynamic.yml
    chmod 0644 /etc/traefik/dynamic.yml
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
    chmod -R a+rX /opt/printhub
}

# write_config: asks for the settings on the first run and sets WEB_HOST from the config on every run.
write_config() {
    local cfg=/etc/printhub/config.toml pw pw2 hash secret host port security user smtp_pw from content
    if [[ -e $cfg ]]; then
        WEB_HOST=$(config_host "$cfg")
        if [[ -n $WEB_HOST ]]; then
            valid_hostname "$WEB_HOST" || die "origin in $cfg must be https://<hostname>, got host $WEB_HOST"
            return 0
        fi
        # Configs written before the hostname was asked for have no origin.
        ask_host
        add_origin "$cfg" "$WEB_HOST" | write_secret "$cfg" printhub:printhub
        return 0
    fi
    ask_host
    ask_secret pw "Web password"
    ask_secret pw2 "Repeat web password"
    [[ $pw == "$pw2" ]] || die "passwords differ"
    # Hash from the checkout; the password goes through stdin, never argv.
    hash=$(printf %s "$pw" | PYTHONPATH=$REPO python3 -c \
        'import sys; from printhub.auth import hash_password; print(hash_password(sys.stdin.read()))')
    secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
    ask host "SMTP host"
    ask port "SMTP port" 587 valid_port
    ask security "SMTP security (starttls or tls)" starttls valid_security
    ask user "SMTP user"
    ask_secret smtp_pw "SMTP password"
    ask from "Mail From address" "$user"

    content=$(PASSWORD_HASH=$hash SESSION_SECRET=$secret SMTP_HOST=$host SMTP_PORT=$port \
        SMTP_SECURITY=$security SMTP_USER=$user SMTP_PASSWORD=$smtp_pw MAIL_FROM=$from WEB_HOST=$WEB_HOST python3 -c '
import json, os
for k in ("password_hash", "session_secret", "smtp_host", "smtp_security", "smtp_user", "smtp_password", "mail_from"):
    print(k, "=", json.dumps(os.environ[k.upper()]))
print("smtp_port =", int(os.environ["SMTP_PORT"]))
print("origin =", json.dumps("https://" + os.environ["WEB_HOST"]))')
    install -d -m 0750 -o root -g printhub /etc/printhub
    printf '%s\n' "$content" | write_secret "$cfg" printhub:printhub
}

write_traefik_env() {
    local env=/etc/traefik/traefik.env email_file=/etc/traefik/acme-email token email content
    if [[ ! -e $env ]]; then
        ask_secret token "Cloudflare API token (Zone > Zone > Read AND Zone > DNS > Edit, scoped to the zone of $WEB_HOST)"
        printf 'CF_DNS_API_TOKEN=%s\n' "$token" | write_secret "$env" root:root
    else
        # Drop the obsolete TRAEFIK_* setting: static file options must live in traefik.yml.
        content=$(grep '^CF_DNS_API_TOKEN=' "$env")
        printf '%s\n' "$content" | write_secret "$env" root:root
    fi
    if [[ ! -e $email_file ]]; then
        ask email "ACME account email"
        printf '%s\n' "$email" | write_secret "$email_file" root:root
    fi
}

start_services() {
    if show_job_details /etc/cups/cupsd.conf; then
        systemctl restart cups
    fi
    install -m 0644 "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.timer /etc/systemd/system/
    systemctl daemon-reload
    # Pick up new code and config on re-runs; enable --now then starts whatever was not running.
    systemctl try-restart printhub traefik airsaned
    systemctl enable --now printhub traefik airsaned printhub-retention.timer
}

print_network_todo() {
    local dev addr mac
    read -r dev addr < <(ip -4 -o route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) { if ($i == "dev") d = $(i + 1); if ($i == "src") s = $(i + 1) } print d, s}') || true
    if [[ -z ${dev:-} || -z ${addr:-} ]]; then
        echo "Done. No default route found; add the LAN DNS override and DHCP reservation for $WEB_HOST yourself."
        return 0
    fi
    mac=$(<"/sys/class/net/$dev/address")
    cat <<MSG

Done. Two things remain on your router/DNS:
  1. LAN DNS override: $WEB_HOST -> $addr
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
    install_traefik_config
    start_services
    ((${PRINTER_PENDING:-0})) && echo "Printer not set up yet: plug it in, then re-run: sudo $REPO/deploy/install.sh"
    print_network_todo
}

# Sourcing the file (for tests) must not install anything.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi

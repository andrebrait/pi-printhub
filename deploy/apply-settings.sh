#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Applies the sharing switches the web app asked for in $DATA_DIR/system-request.json, as root.
# Run by printhub-apply.service when printhub-apply.path sees the request change, and with the argument "firewall"
# by printhub-firewall.service at boot. The app user owns the request, so
# it is untrusted input: read_request checks it before anything is applied.

# _request: validate the request file and print "share", "expose_usb" and "at", one per line (no final newline).
# An absent file means "everything on", the state install.sh sets up. Opened without following symlinks, and every
# check is on the open file, so the app user cannot swap the file between check and read.
_request() {
    DATA_DIR=${DATA_DIR:-/var/lib/printhub} OWNER=${OWNER:-printhub} python3 -c '
import json, os, pwd, stat, sys

def refuse(reason):
    sys.exit(f"system request refused: {reason}")

path = os.path.join(os.environ["DATA_DIR"], "system-request.json")
try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except FileNotFoundError:
    print("true\ntrue\n", end="")
    sys.exit(0)
except OSError as e:
    refuse(f"cannot open without following links ({e.strerror})")
with os.fdopen(fd, "rb") as f:
    st = os.fstat(f.fileno())
    if not stat.S_ISREG(st.st_mode):
        refuse("not a regular file")
    try:
        owner = pwd.getpwnam(os.environ["OWNER"]).pw_uid
    except KeyError:
        refuse("owner " + os.environ["OWNER"] + " does not exist")
    if st.st_uid != owner:
        refuse("wrong owner")
    data = f.read(1025)
if len(data) > 1024:
    refuse("larger than 1024 bytes")
try:
    req = json.loads(data)
except ValueError:
    refuse("not JSON")
if not (
    isinstance(req, dict)
    and set(req) == {"share", "expose_usb", "at"}
    and type(req["share"]) is bool
    and type(req["expose_usb"]) is bool
    and isinstance(req["at"], str)
):
    refuse("not exactly {share: bool, expose_usb: bool, at: string}")
share, expose, at = req["share"], req["expose_usb"], req["at"]
print(f"{str(share).lower()}\n{str(expose).lower()}\n{at}", end="")
'
}

# _load_request: sets REQ_SHARE, REQ_EXPOSE (true|false) and REQ_AT (the request timestamp, empty if none).
_load_request() {
    local out
    out=$(_request && echo x) || return 1
    out=${out%x}
    REQ_SHARE=${out%%$'\n'*}
    out=${out#*$'\n'}
    REQ_EXPOSE=${out%%$'\n'*}
    REQ_AT=${out#*$'\n'}
}

# read_request: print "share expose_usb" as true|false words; non-zero with the reason on stderr if unsafe.
read_request() {
    _load_request || return 1
    echo "$REQ_SHARE $REQ_EXPOSE"
}

# must CMD...: run CMD; on failure say which command, so the status error is never empty.
must() { "$@" || { echo "$1 failed (exit $?)" >&2; return 1; }; }

# _ipp_usb_ports: "MIN-MAX", ipp-usb's HTTP port range from its conf. ipp-usb.conf is root's, but a value that is not
# 1-65535 digits with MIN <= MAX falls back to ipp-usb's defaults rather than reach nft.
_ipp_usb_ports() {
    local conf=${IPP_USB_CONF:-/etc/ipp-usb/ipp-usb.conf} line min=60000 max=65535 lo="" hi=""
    if [[ -r $conf ]]; then
        while IFS= read -r line || [[ -n $line ]]; do
            [[ $line =~ ^[[:space:]]*http-min-port[[:space:]]*=[[:space:]]*([0-9]{1,5})[[:space:]]*(#.*)?$ ]] && lo=${BASH_REMATCH[1]}
            [[ $line =~ ^[[:space:]]*http-max-port[[:space:]]*=[[:space:]]*([0-9]{1,5})[[:space:]]*(#.*)?$ ]] && hi=${BASH_REMATCH[1]}
        done <"$conf"
    fi
    if [[ -n $lo && -n $hi ]] && ((10#$lo >= 1 && 10#$hi <= 65535 && 10#$lo <= 10#$hi)); then
        min=$((10#$lo)) max=$((10#$hi))
    fi
    echo "$min-$max"
}

# _set_firewall true|false: drop NEW TCP connections from other hosts to ipp-usb's ports (false), or allow them (true).
# The table is recreated in one atomic nft transaction. Only new connections are matched, and not on lo: ipp-usb's
# range overlaps the local ephemeral ports, whose replies must pass. ipp-usb is never restarted here: that can leave
# the printer's USB interface claimed until it is power-cycled.
_set_firewall() {
    local rule=""
    # nft wants a newline or ';' before each closing brace.
    [[ $1 == true ]] || rule="table inet printhub {
  chain input {
    type filter hook input priority filter; policy accept;
    iifname != \"lo\" ct state new tcp dport $(_ipp_usb_ports) drop
  }
}"
    must nft -f - <<<"table inet printhub {}
delete table inet printhub
$rule"
}

# apply_expose true|false: the firewall now, and ipp-usb's own interface line (all or loopback) for its next start,
# which the printer's power-on or the Pi's boot brings.
apply_expose() {
    local conf=${IPP_USB_CONF:-/etc/ipp-usb/ipp-usb.conf} want=loopback
    [[ $1 == true ]] && want=all
    _set_firewall "$1" || return 1
    # No ipp-usb (an HPLIP or printerless install): no conf to keep in step.
    [[ -e $conf ]] || return 0
    grep -q '^[[:space:]]*interface[[:space:]]*=' "$conf" || { echo "no interface line in $conf" >&2; return 1; }
    must sed -i "s/^\([[:space:]]*interface[[:space:]]*=[[:space:]]*\)[^[:space:]#]*/\1$want/" "$conf"
}

# apply_firewall: at boot, the firewall part of the saved request only. An unreadable request means the defaults.
apply_firewall() {
    REQ_EXPOSE=true
    _load_request || echo "using the default: the printer's USB interface is exposed" >&2
    _set_firewall "$REQ_EXPOSE"
}

# airsane enable|disable: AirSane's unit follows the sharing switch; absent before install.sh has built it.
airsane() {
    systemctl cat airsaned >/dev/null 2>&1 || return 0
    must systemctl "$1" --now airsaned || return 1
    # AirSane segfaults on SIGTERM, which leaves the stopped unit marked failed.
    if [[ $1 == disable ]]; then systemctl reset-failed airsaned || true; fi
}

# apply_share true|false: the CUPS queue's sharing, its Bonjour announcements and AirSane (the network scanner).
# Without a queue (no printer set up yet) there is nothing for CUPS to share, and lpadmin would create one.
apply_share() {
    local queue=${PRINTER_QUEUE:-OfficeJet} avahi=${AVAHI_DIR:-/etc/avahi/services} uri="" have_queue=0
    lpstat -p "$queue" >/dev/null 2>&1 && have_queue=1
    if [[ $1 == true ]]; then
        if ((have_queue)); then
            uri=$(lpstat -v "$queue" 2>/dev/null) || uri=""
            if [[ $uri == *//localhost:6????/* && ! -e $avahi/printhub-airprint.service ]]; then
                # Shared again after being unshared: printer-setup.sh writes the announcements, which cupsctl cannot.
                must systemctl start printhub-printer.service || return 1
            else
                must lpadmin -p "$queue" -o printer-is-shared=true || return 1
                must cupsctl --share-printers || return 1
            fi
        fi
        airsane enable
    else
        if ((have_queue)); then
            must lpadmin -p "$queue" -o printer-is-shared=false || return 1
            must cupsctl --no-share-printers || return 1
        fi
        rm -f "$avahi/printhub-airprint.service" "$avahi/printhub-generic.service" || return 1
        airsane disable
    fi
}

# write_status applied|failed SHARE EXPOSE ERROR REQUEST_AT: the file the web app reads. Written by fd, not by
# path, because the app user owns the directory.
write_status() {
    DATA_DIR=${DATA_DIR:-/var/lib/printhub} OWNER=${OWNER:-printhub} \
        STATE=$1 SHARE=$2 EXPOSE=$3 ERROR=$4 REQUEST_AT=$5 python3 -c '
import json, os, pwd, tempfile
from datetime import datetime, timezone

e = os.environ
user = pwd.getpwnam(e["OWNER"])
status = {
    "state": e["STATE"],
    "share": e["SHARE"] == "true",
    "expose_usb": e["EXPOSE"] == "true",
    "error": e["ERROR"] or None,
    "at": datetime.now(timezone.utc).isoformat(),
    "request_at": e["REQUEST_AT"] or None,
}
fd, tmp = tempfile.mkstemp(dir=e["DATA_DIR"], prefix=".system-status.")
try:
    os.fchown(fd, user.pw_uid, user.pw_gid)
    with os.fdopen(fd, "w") as f:
        json.dump(status, f)
    os.replace(tmp, os.path.join(e["DATA_DIR"], "system-status.json"))
except BaseException:
    os.unlink(tmp)
    raise
'
}

# current_share, current_expose: what the system is set to now, for the status after a failure.
# CUPS 2.4's lpstat prints no sharing flag; lpoptions does, as the token printer-is-shared=true. Quoted values
# (description, location) are dropped first so their text cannot match.
current_share() {
    local out
    out=$(lpoptions -p "${PRINTER_QUEUE:-OfficeJet}" 2>/dev/null | sed "s/'[^']*'//g") || out=""
    [[ $out =~ (^|[[:space:]])printer-is-shared=true([[:space:]]|$) ]] && echo true || echo false
}
current_expose() {
    local rules
    rules=$(nft list table inet printhub 2>/dev/null) || rules=""
    [[ $rules == *drop* ]] && echo false || echo true
}

# apply_request: read the request and apply it. Every step is checked explicitly: set -e is off inside the
# `if` that run_apply puts this in.
apply_request() {
    _load_request || return 1
    apply_expose "$REQ_EXPOSE" || return 1
    apply_share "$REQ_SHARE"
}

# run_apply: up to three passes, because the web app can write a newer request while one is being applied and the
# path unit may not start the service again for it. The status carries the last request applied.
run_apply() {
    local errf error share expose at failed
    REQ_AT=""
    if ! errf=$(mktemp); then
        _load_request 2>/dev/null || true
        write_status failed "$(current_share)" "$(current_expose)" "mktemp failed" "$REQ_AT"
        return 1
    fi
    # shellcheck disable=SC2064 # errf is local and gone when the trap fires, so expand it now
    trap "rm -f '$errf'" EXIT
    for _ in 1 2 3; do
        REQ_SHARE="" REQ_EXPOSE="" REQ_AT="" failed=0
        apply_request 2>"$errf" || failed=1
        # A failure before _load_request knows no timestamp to copy.
        share=$REQ_SHARE expose=$REQ_EXPOSE at=$REQ_AT
        # The app may have written a newer request meanwhile, and the path unit does not fire while this runs: that one
        # is applied too, after a failed pass as well as after a good one. An unreadable request counts as changed
        # after a good pass (the next one fails on it and says why), and as final after a failed one.
        if _load_request 2>/dev/null; then
            [[ $REQ_AT == "$at" ]] && break
        else
            ((failed)) && break
        fi
    done
    if ((failed)); then
        error=$(<"$errf")
        rm -f "$errf"
        echo "${error:0:500}" >&2 # the installer's own run shows why
        write_status failed "$(current_share)" "$(current_expose)" "${error:0:500}" "$at"
        return 1
    fi
    write_status applied "$share" "$expose" "" "$at"
    rm -f "$errf"
}

# Sourcing the file (for tests and printer-setup.sh) must not change anything.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    set -euo pipefail
    case ${1:-} in
        "") run_apply ;;
        firewall) apply_firewall ;;
        *) echo "usage: apply-settings.sh [firewall]" >&2; exit 2 ;;
    esac
fi

#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Applies the sharing switches the web app asked for in $DATA_DIR/system-request.json, as root.
# Run by printhub-apply.service when printhub-apply.path sees the request change. The app user owns the request, so
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

# apply_expose true|false: ipp-usb serves the printer's own IPP and eSCL on the LAN (all) or only to the Pi (loopback).
apply_expose() {
    local conf=${IPP_USB_CONF:-/etc/ipp-usb/ipp-usb.conf} want=loopback before after
    [[ $1 == true ]] && want=all
    grep -q '^[[:space:]]*interface[[:space:]]*=' "$conf" || { echo "no interface line in $conf" >&2; return 1; }
    before=$(<"$conf") || return 1
    must sed -i "s/^\([[:space:]]*interface[[:space:]]*=[[:space:]]*\)[^[:space:]]*/\1$want/" "$conf" || return 1
    after=$(<"$conf") || return 1
    [[ $before == "$after" ]] || must systemctl try-restart ipp-usb
}

# apply_share true|false: the CUPS queue's sharing, its Bonjour announcements and AirSane (the network scanner).
apply_share() {
    local queue=${PRINTER_QUEUE:-OfficeJet} avahi=${AVAHI_DIR:-/etc/avahi/services} uri=""
    if [[ $1 == true ]]; then
        uri=$(lpstat -v "$queue" 2>/dev/null) || uri=""
        if [[ $uri == *//localhost:6????/* && ! -e $avahi/printhub-airprint.service ]]; then
            # Shared again after being unshared: printer-setup.sh writes the announcements, which cupsctl cannot.
            must systemctl start printhub-printer.service || return 1
        else
            must lpadmin -p "$queue" -o printer-is-shared=true || return 1
            must cupsctl --share-printers || return 1
        fi
        must systemctl enable --now airsaned
    else
        must lpadmin -p "$queue" -o printer-is-shared=false || return 1
        must cupsctl --no-share-printers || return 1
        rm -f "$avahi/printhub-airprint.service" "$avahi/printhub-generic.service" || return 1
        must systemctl disable --now airsaned
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
current_share() {
    local out
    out=$(lpstat -l -p "${PRINTER_QUEUE:-OfficeJet}" 2>/dev/null) || out=""
    [[ $out == *shared* && ${out,,} != *"not shared"* ]] && echo true || echo false
}
current_expose() {
    grep -q '^[[:space:]]*interface[[:space:]]*=[[:space:]]*all' "${IPP_USB_CONF:-/etc/ipp-usb/ipp-usb.conf}" 2>/dev/null &&
        echo true || echo false
}

# apply_request: read the request and apply it. Every step is checked explicitly: set -e is off inside the
# `if` that run_apply puts this in.
apply_request() {
    _load_request || return 1
    apply_expose "$REQ_EXPOSE" || return 1
    apply_share "$REQ_SHARE"
}

run_apply() {
    local errf error
    REQ_AT=""
    errf=$(mktemp)
    if apply_request 2>"$errf"; then
        write_status applied "$REQ_SHARE" "$REQ_EXPOSE" "" "$REQ_AT"
        rm -f "$errf"
    else
        error=$(<"$errf")
        rm -f "$errf"
        # A failure after _load_request knows the request's timestamp; one before it has none to copy.
        write_status failed "$(current_share)" "$(current_expose)" "${error:0:500}" "$REQ_AT"
        return 1
    fi
}

# Sourcing the file (for tests and printer-setup.sh) must not change anything.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    set -euo pipefail
    run_apply
fi

#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# ippeveprinter -c command: log one tab-separated line per job: the IPP_*
# environment plus FILE=<spooled document> (ippeveprinter passes it as $1).
log=${PRINTHUB_JOB_LOG:-/tmp/printhub-jobs.log}
{ env | grep '^IPP_' | sort; echo "FILE=$1"; } | tr '\n' '\t' >> "$log"
echo >> "$log"

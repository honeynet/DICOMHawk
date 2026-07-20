#!/usr/bin/env bash
set -euo pipefail

: "${DICOMHAWK_TRACES_HOST_PATH:?set DICOMHAWK_TRACES_HOST_PATH}"
: "${DICOMHAWK_STATE_HOST_PATH:?set DICOMHAWK_STATE_HOST_PATH}"
: "${DICOMHAWK_LOGS_HOST_PATH:?set DICOMHAWK_LOGS_HOST_PATH}"
: "${DICOMHAWK_TRACE_FILESYSTEM_MAX_BYTES:?set the maximum permitted trace-filesystem capacity}"

for path in "$DICOMHAWK_TRACES_HOST_PATH" "$DICOMHAWK_STATE_HOST_PATH" "$DICOMHAWK_LOGS_HOST_PATH"; do
    [[ -d $path ]] || { echo "Not a directory: $path" >&2; exit 1; }
    owner=$(stat -c %u:%g "$path")
    [[ $owner == 999:999 ]] || { echo "Expected UID:GID 999:999 on $path, got $owner" >&2; exit 1; }
done

trace_device=$(stat -c %d "$DICOMHAWK_TRACES_HOST_PATH")
state_device=$(stat -c %d "$DICOMHAWK_STATE_HOST_PATH")
log_device=$(stat -c %d "$DICOMHAWK_LOGS_HOST_PATH")
[[ $trace_device != "$state_device" && $trace_device != "$log_device" ]] || {
    echo "Traces must be on a different filesystem from state and logs." >&2
    exit 1
}

[[ $DICOMHAWK_TRACE_FILESYSTEM_MAX_BYTES =~ ^[0-9]+$ ]] || {
    echo "DICOMHAWK_TRACE_FILESYSTEM_MAX_BYTES must be an integer." >&2
    exit 1
}
trace_capacity=$(df -B1 --output=size "$DICOMHAWK_TRACES_HOST_PATH" | tail -n 1 | tr -d ' ')
[[ $trace_capacity =~ ^[0-9]+$ && $trace_capacity -le $DICOMHAWK_TRACE_FILESYSTEM_MAX_BYTES ]] || {
    echo "Trace filesystem capacity $trace_capacity exceeds $DICOMHAWK_TRACE_FILESYSTEM_MAX_BYTES." >&2
    exit 1
}

"$(dirname "$0")/lockdown-egress.sh" check
echo "Production preflight passed."

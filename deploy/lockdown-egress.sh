#!/usr/bin/env bash
set -euo pipefail

ACTION=${1:-check}
NETWORK=${DICOMHAWK_DOCKER_NETWORK:-dicomhawk_dicomhawk_net}
COMMENT=dicomhawk-egress

if [[ $EUID -ne 0 && ${DICOMHAWK_FIREWALL_TEST:-0} != 1 ]]; then
    echo "Run as root so firewall rules can be inspected or changed." >&2
    exit 1
fi

network_id=$(docker network inspect -f '{{.Id}}' "$NETWORK")
bridge="br-${network_id:0:12}"
mapfile -t subnets < <(docker network inspect -f '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' "$NETWORK")

# Match installed rules from -S output, not by -C reconstruction the kernel's rule normalisation defeats.
present() {  # <tool> <chain> <subnet> <target>
    "$1" -S "$2" 2>/dev/null \
        | grep -F -- "--comment $COMMENT" \
        | grep -F -- "-s $3 " \
        | grep -qF -- "-j $4"
}

# Assemble with -m comment before -j, the order the kernel stores.
edit() {  # <tool> <op> <chain> <target> <match-args...>
    local tool=$1 op=$2 chain=$3 target=$4
    shift 4
    "$tool" "$op" "$chain" "$@" -m comment --comment "$COMMENT" -j "$target"
}

# Delete verbatim: reissue the kernel's own -A line as -D, re-reading until none are left.
remove_all() {  # <tool> <chain> <subnet>
    local tool=$1 chain=$2 subnet=$3 spec rest guard=0
    local prefix="-A $chain "
    while :; do
        # || true: pipefail makes a no-match grep non-zero, which set -e would treat as fatal.
        spec=$("$tool" -S "$chain" 2>/dev/null \
            | grep -F -- "--comment $COMMENT" \
            | grep -F -- "-s $subnet " \
            | head -n1) || true
        [[ -n $spec ]] || break
        rest=${spec#"$prefix"}
        # shellcheck disable=SC2086  # rest is a rule spec that must word-split into args
        "$tool" -D "$chain" $rest || break
        if (( ++guard >= 64 )); then break; fi
    done
}

missing=0
for subnet in "${subnets[@]}"; do
    [[ -n $subnet ]] || continue
    tool=iptables
    [[ $subnet == *:* ]] && tool=ip6tables
    established=(-s "$subnet" -m conntrack --ctstate RELATED,ESTABLISHED)
    drop_forward=(-s "$subnet")
    drop_host=(-i "$bridge" -s "$subnet" -m conntrack --ctstate NEW)

    case "$ACTION" in
        apply)
            # DROP first, then RETURN, both inserted at the head, so evaluation runs RETURN→DROP.
            present "$tool" DOCKER-USER "$subnet" DROP   || edit "$tool" -I DOCKER-USER DROP   "${drop_forward[@]}"
            present "$tool" DOCKER-USER "$subnet" RETURN || edit "$tool" -I DOCKER-USER RETURN "${established[@]}"
            present "$tool" INPUT       "$subnet" DROP   || edit "$tool" -I INPUT       DROP   "${drop_host[@]}"
            ;;
        check)
            present "$tool" DOCKER-USER "$subnet" RETURN || missing=1
            present "$tool" DOCKER-USER "$subnet" DROP   || missing=1
            present "$tool" INPUT       "$subnet" DROP   || missing=1
            ;;
        remove)
            remove_all "$tool" DOCKER-USER "$subnet"
            remove_all "$tool" INPUT       "$subnet"
            ;;
        *)
            echo "Usage: $0 apply|check|remove" >&2
            exit 2
            ;;
    esac
done

if [[ $ACTION == check && $missing -ne 0 ]]; then
    echo "DICOMHawk egress rules: MISSING for $NETWORK" >&2
    exit 1
fi

echo "DICOMHawk egress rules: $ACTION complete for $NETWORK"

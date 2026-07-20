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

rule() {
    local tool=$1 chain=$2 operation=$3
    shift 3
    "$tool" "$operation" "$chain" "$@" -m comment --comment "$COMMENT"
}

for subnet in "${subnets[@]}"; do
    [[ -n $subnet ]] || continue
    tool=iptables
    [[ $subnet == *:* ]] && tool=ip6tables
    established=(-s "$subnet" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN)
    drop_forward=(-s "$subnet" -j DROP)
    drop_host=(-i "$bridge" -s "$subnet" -m conntrack --ctstate NEW -j DROP)

    case "$ACTION" in
        apply)
            rule "$tool" DOCKER-USER -C "${drop_forward[@]}" 2>/dev/null || rule "$tool" DOCKER-USER -I "${drop_forward[@]}"
            rule "$tool" DOCKER-USER -C "${established[@]}" 2>/dev/null || rule "$tool" DOCKER-USER -I "${established[@]}"
            rule "$tool" INPUT -C "${drop_host[@]}" 2>/dev/null || rule "$tool" INPUT -I "${drop_host[@]}"
            ;;
        check)
            rule "$tool" DOCKER-USER -C "${established[@]}"
            rule "$tool" DOCKER-USER -C "${drop_forward[@]}"
            rule "$tool" INPUT -C "${drop_host[@]}"
            ;;
        remove)
            rule "$tool" DOCKER-USER -D "${established[@]}" 2>/dev/null || true
            rule "$tool" DOCKER-USER -D "${drop_forward[@]}" 2>/dev/null || true
            rule "$tool" INPUT -D "${drop_host[@]}" 2>/dev/null || true
            ;;
        *)
            echo "Usage: $0 apply|check|remove" >&2
            exit 2
            ;;
    esac
done

echo "DICOMHawk egress rules: $ACTION complete for $NETWORK"

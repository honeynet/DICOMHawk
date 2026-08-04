#!/usr/bin/env bash
# Guided first-run installer. See docs/installation.md.
set -euo pipefail

# Builtins only: a missing dirname on PATH would silently resolve the repo to the caller's cwd.
_self=${BASH_SOURCE[0]}
_dir=${_self%/*}
[[ $_dir == "$_self" ]] && _dir=.
REPO_ROOT=$(cd -- "$_dir" && pwd) || exit 1
ENV_FILE="$REPO_ROOT/.env"
ENV_EXAMPLE="$REPO_ROOT/.env.example"
OVERRIDE_FILE="$REPO_ROOT/docker-compose.override.yml"

# The `ports: !override` tag the generated override depends on landed in Compose 2.24.
MIN_COMPOSE="2.24"

# Base docker-compose.yml publishes these; the override only has to exist when they change.
DEFAULT_PORTS="104"
DEFAULT_WEB_PORT="8080"
DEFAULT_OPERATOR_PORT="8081"

# Republished verbatim: !override replaces the whole list, and which ports exist is fingerprint.
DICOMWEB_PUBLISHED=(8042 9080 10080 12080 13080)

# Raise it on a slow host: the first start also builds the database and opens the listeners.
HEALTH_TIMEOUT="${DICOMHAWK_HEALTH_TIMEOUT:-120}"

USE_DEFAULTS=0
DO_START=1
DO_SEED=1
RECONFIGURE=0
NO_INSTALL=0

# Both are rebound once access is known: sudo drops out under root, docker gains it without a group.
SUDO=()
[[ $EUID -eq 0 ]] || SUDO=(sudo)
DOCKER=(docker)

# One EXIT trap: a per-function RETURN trap is global in bash and refires out of scope.
PREVIEW_FILE=""
SOURCES_FILE=""
cleanup() { rm -f "$REPO_ROOT"/.env.tmp.* "$PREVIEW_FILE" "$SOURCES_FILE" 2>/dev/null || true; }
trap cleanup EXIT

red() { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
info() { printf '\033[0;34m==>\033[0m %s\n' "$*"; }
die() { red "$*"; exit 1; }

usage() {
    cat <<'EOF'
Usage: ./setup.sh [options]

  --defaults      Accept every default without prompting (no whiptail needed).
  --no-start      Write the configuration and stop; do not build or start anything.
  --no-seed       Build and start, but skip the initial seed.
  --reconfigure   Overwrite an existing .env without asking first.
  --no-install    Refuse if a prerequisite is missing instead of installing it.
  -h, --help      Show this message.
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --defaults) USE_DEFAULTS=1 ;;
        --no-start) DO_START=0 ;;
        --no-seed) DO_SEED=0 ;;
        --reconfigure) RECONFIGURE=1 ;;
        --no-install) NO_INSTALL=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "Unknown option: $1" ;;
    esac
    shift
done

# ---- answers ----

# Exported variable, then the existing .env, then the default; reading .env back keeps a token.
declare -A SAVED=()
if [[ -f $ENV_FILE ]]; then
    while IFS= read -r saved_line || [[ -n $saved_line ]]; do
        [[ $saved_line =~ ^(DICOMHAWK_[A-Z_]+)=(.*)$ ]] || continue
        SAVED[${BASH_REMATCH[1]}]=${BASH_REMATCH[2]}
    done <"$ENV_FILE"
fi

answer() {  # <variable-name> <fallback>
    printf '%s' "${!1:-${SAVED[$1]:-$2}}"
}

PROFILE=$(answer DICOMHAWK_PROFILE "")
AE_TITLE=$(answer DICOMHAWK_AE_TITLE "")
PORTS=$(answer DICOMHAWK_PORTS "$DEFAULT_PORTS")
WEB_PORT=$(answer DICOMHAWK_WEB_PORT "$DEFAULT_WEB_PORT")
OPERATOR_PORT=$(answer DICOMHAWK_OPERATOR_PORT "$DEFAULT_OPERATOR_PORT")
OPERATOR_TOKEN=$(answer DICOMHAWK_OPERATOR_TOKEN "")
BACKEND_SERVER=$(answer DICOMHAWK_BACKEND_SERVER "SYNWEB01")
PUBLIC_BASE_URL=$(answer DICOMHAWK_PUBLIC_BASE_URL "")
TRUSTED_PROXY=$(answer DICOMHAWK_TRUSTED_PROXY "")
SECURE_COOKIES=$(answer DICOMHAWK_SECURE_COOKIES "")
ANALYSIS=$(answer DICOMHAWK_ANALYSIS "true")
FINGERPRINT=$(answer DICOMHAWK_FINGERPRINT "true")

SEED_COLLECTION="TCGA-LUAD"
SEED_MODALITY="CT"
SEED_MAX_SERIES="3"
SEED_MAX_IMAGES="30"
SEED_LOCALE="en_US"
SEED_OSM_CITY=""
SEED_OSM_COUNTRY=""
SEED_HONEY_URL=""
SEED_CANARY_PDF=""
USE_OSM=0

# ---- prerequisites ----

# Compare dotted versions without assuming a two-part shape; sort -V handles 2.24 vs 2.9.
version_below() {  # <have> <want>
    [[ $1 != "$2" ]] && [[ $(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n 1) == "$1" ]]
}

compose_version() { docker compose version --short 2>/dev/null | sed 's/^v//'; }

# A fresh group membership never reaches an already-running shell, so fall back instead of failing.
resolve_docker_access() {
    docker info >/dev/null 2>&1 && return 0
    "${SUDO[@]}" docker info >/dev/null 2>&1 || return 1
    DOCKER=("${SUDO[@]}" docker)
    red "Using sudo for Docker. Log out and back in to use the 'docker' group directly."
}

docker_is_current() {
    command -v docker >/dev/null 2>&1 || return 1
    local have
    have=$(compose_version) || return 1
    [[ -n $have ]] && ! version_below "$have" "$MIN_COMPOSE"
}

# Docker's own documented apt procedure; distribution packages lag the Compose version we need.
install_docker() {
    local codename distro
    codename=$(. /etc/os-release && echo "${VERSION_CODENAME:-}")
    distro=$(. /etc/os-release && echo "${ID:-}")
    [[ -n $codename && -n $distro ]] || die "Cannot determine the Ubuntu/Debian release from /etc/os-release."

    info "Installing Docker Engine and the Compose plugin…"
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" apt-get install -y ca-certificates curl
    "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
    "${SUDO[@]}" curl -fsSL "https://download.docker.com/linux/$distro/gpg" \
        -o /etc/apt/keyrings/docker.asc
    "${SUDO[@]}" chmod a+r /etc/apt/keyrings/docker.asc
    # Staged then installed rather than piped into tee: a pipeline here dies on SIGPIPE under pipefail.
    SOURCES_FILE=$(mktemp)
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
        "$(dpkg --print-architecture)" "$distro" "$codename" >"$SOURCES_FILE"
    "${SUDO[@]}" install -m 0644 "$SOURCES_FILE" /etc/apt/sources.list.d/docker.list
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" apt-get install -y \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    "${SUDO[@]}" systemctl enable --now docker || true
    # Without this every later docker call needs sudo, including the ones an operator runs by hand.
    [[ $EUID -eq 0 ]] || "${SUDO[@]}" usermod -aG docker "$USER" || true
}

install_prerequisites() {
    local missing=()
    docker_is_current || missing+=("Docker Engine with Compose $MIN_COMPOSE or newer")
    (( USE_DEFAULTS )) || command -v whiptail >/dev/null 2>&1 || missing+=("whiptail")
    (( ${#missing[@]} )) || return 0

    if (( NO_INSTALL )); then
        die "Missing: ${missing[*]}. Install them, or drop --no-install to let this script do it."
    fi
    command -v apt-get >/dev/null 2>&1 || die \
        "Missing: ${missing[*]}. Automatic installation only supports Debian/Ubuntu; install them by hand."
    [[ $EUID -eq 0 ]] || command -v sudo >/dev/null 2>&1 || die \
        "Missing: ${missing[*]}, and sudo is unavailable. Re-run as root or install them by hand."

    red "The following need to be installed: ${missing[*]}"
    if (( ! USE_DEFAULTS )) && [[ -t 0 ]]; then
        read -r -p "Install them now? [Y/n] " reply
        [[ ${reply:-Y} =~ ^[Yy]?$ ]] || die "Nothing installed."
    fi

    docker_is_current || install_docker
    if (( ! USE_DEFAULTS )) && ! command -v whiptail >/dev/null 2>&1; then
        "${SUDO[@]}" apt-get install -y whiptail
    fi
}

check_prerequisites() {
    command -v docker >/dev/null 2>&1 || die "Docker is not installed. See docs/installation.md."

    resolve_docker_access || die \
        "Cannot talk to the Docker daemon. Start it, or add yourself to the 'docker' group and log back in."

    local compose
    compose=$(compose_version)
    [[ -n $compose ]] || die "The Docker Compose plugin is missing. Install docker-compose-plugin."
    if version_below "$compose" "$MIN_COMPOSE"; then
        die "Docker Compose $compose is too old; $MIN_COMPOSE or newer is required for the generated port override."
    fi

    [[ -f $ENV_EXAMPLE ]] || die "Missing $ENV_EXAMPLE. Run this from a complete checkout."

    if [[ $EUID -eq 0 ]]; then
        red "Warning: running as root. The honeypot itself runs unprivileged; only the firewall steps in docs/deployment.md need root."
    fi

    if (( ! USE_DEFAULTS )); then
        command -v whiptail >/dev/null 2>&1 \
            || die "whiptail is not installed. Install it (apt install whiptail) or re-run with --defaults."
        [[ -t 0 && -t 1 ]] \
            || die "Not running on a terminal. Re-run with --defaults for an unattended install."
    fi

    green "Docker and Compose $compose are ready."
}

# Warn rather than refuse: the port may be held by a container this script is about to replace.
warn_busy_ports() {
    command -v ss >/dev/null 2>&1 || return 0
    local listening port
    listening=$(ss -ltnH 2>/dev/null | awk '{print $4}' | sed 's/.*://') || return 0
    for port in $(printf '%s\n' "${PORTS//,/ }") "$WEB_PORT" "$OPERATOR_PORT"; do
        if printf '%s\n' "$listening" | grep -qx -- "$port"; then
            red "Warning: port $port is already in use; 'docker compose up' will fail unless it is freed."
        fi
    done
}

# ---- validation ----

valid_port() { [[ $1 =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 )); }

valid_port_list() {  # comma-separated, at least one, no duplicates
    local seen=" " port
    [[ -n $1 ]] || return 1
    for port in ${1//,/ }; do
        valid_port "$port" || return 1
        [[ $seen == *" $port "* ]] && return 1
        seen+="$port "
    done
    return 0
}

# Rejected up front: a bad port only surfaces as an opaque `compose up` failure minutes later.
reject_port() {  # <label> <value>
    local message="$2 is not a usable $1. Give a number between 1 and 65535"
    [[ $1 == "DIMSE port list" ]] && message+=", or several separated by commas, with no repeats"
    if (( USE_DEFAULTS )); then
        die "$message."
    fi
    box --ok-button Retry --msgbox "$message." 10 74 || true
}

# Publishing the same host port twice makes `up` fail with a message about neither of them.
ports_collide() {
    local port seen=" $WEB_PORT $OPERATOR_PORT "
    for port in ${PORTS//,/ }; do
        [[ $seen == *" $port "* ]] && return 0
        seen+="$port "
    done
    [[ $WEB_PORT == "$OPERATOR_PORT" ]]
}

# ---- configuration file ----

# Rewrite .env.example rather than emit our own, so its comments and any new key survive.
write_env() {
    local -A values=(
        [DICOMHAWK_PROFILE]="$PROFILE"
        [DICOMHAWK_AE_TITLE]="$AE_TITLE"
        [DICOMHAWK_PORTS]="$PORTS"
        [DICOMHAWK_WEB_PORT]="$WEB_PORT"
        [DICOMHAWK_OPERATOR_PORT]="$OPERATOR_PORT"
        [DICOMHAWK_OPERATOR_TOKEN]="$OPERATOR_TOKEN"
        [DICOMHAWK_BACKEND_SERVER]="$BACKEND_SERVER"
        [DICOMHAWK_PUBLIC_BASE_URL]="$PUBLIC_BASE_URL"
        [DICOMHAWK_TRUSTED_PROXY]="$TRUSTED_PROXY"
        [DICOMHAWK_SECURE_COOKIES]="$SECURE_COOKIES"
        [DICOMHAWK_ANALYSIS]="$ANALYSIS"
        [DICOMHAWK_FINGERPRINT]="$FINGERPRINT"
    )

    local tmp seen=() key line
    # A partial .env is worse than none: build it beside the target and move it into place.
    tmp=$(mktemp "$REPO_ROOT/.env.tmp.XXXXXX")

    while IFS= read -r line || [[ -n $line ]]; do
        # Also match a commented-out assignment, so an answered optional variable gets enabled.
        if [[ $line =~ ^#?[[:space:]]*(DICOMHAWK_[A-Z_]+)= ]]; then
            key=${BASH_REMATCH[1]}
            if [[ -v values[$key] ]]; then
                seen+=("$key")
                if [[ -n ${values[$key]} ]]; then
                    printf '%s=%s\n' "$key" "${values[$key]}"
                    continue
                fi
                # Keep an unanswered optional variable commented exactly as it shipped.
                [[ $line == \#* ]] && { printf '%s\n' "$line"; continue; }
                printf '%s=\n' "$key"
                continue
            fi
        fi
        printf '%s\n' "$line"
    done <"$ENV_EXAMPLE" >"$tmp"

    # A variable this script owns but the example never listed would otherwise be dropped.
    for key in "${!values[@]}"; do
        [[ -n ${values[$key]} ]] || continue
        printf '%s\n' "${seen[@]:-}" | grep -qx -- "$key" && continue
        printf '%s=%s\n' "$key" "${values[$key]}" >>"$tmp"
    done

    mv "$tmp" "$ENV_FILE"
    # The operator token lives here, so keep it off other accounts on the host.
    chmod 600 "$ENV_FILE"
    green "Wrote $ENV_FILE"
}

write_override() {
    if [[ $PORTS == "$DEFAULT_PORTS" && $WEB_PORT == "$DEFAULT_WEB_PORT" && $OPERATOR_PORT == "$DEFAULT_OPERATOR_PORT" ]]; then
        # Nothing to correct, and leaving a stale file behind would publish the previous run's ports.
        rm -f "$OVERRIDE_FILE"
        return
    fi

    {
        echo "# Generated by setup.sh. Do not edit; re-run the script instead."
        echo "#"
        echo "# Compose appends 'ports' lists when it merges files, so a custom port would be published"
        echo "# alongside the base 104:104 that nothing listens on. '!override' replaces the list instead."
        echo "services:"
        echo "  dicomhawk:"
        echo "    ports: !override"
        local port
        for port in ${PORTS//,/ }; do
            printf '      - "%s:%s"\n' "$port" "$port"
        done
        printf '      - "%s:%s"\n' "$WEB_PORT" "$WEB_PORT"
        # The operator API binds 0.0.0.0 inside the container; the host side stays loopback-only.
        printf '      - "127.0.0.1:%s:%s"\n' "$OPERATOR_PORT" "$OPERATOR_PORT"
        for port in "${DICOMWEB_PUBLISHED[@]}"; do
            printf '      - "%s:%s"\n' "$port" "$port"
        done
    } >"$OVERRIDE_FILE"

    green "Wrote $OVERRIDE_FILE"
}

# ---- questions ----

TITLE="DICOMHawk Setup"
BACKTITLE="DICOMHawk, a DICOM honeypot"

# whiptail draws on stdout and answers on stderr; fd 3 swaps them so only the answer is captured.
box() { whiptail --title "$TITLE" --backtitle "$BACKTITLE" --cancel-button Back "$@" 3>&1 1>&2 2>&3; }

cancelled() { red "Setup cancelled; nothing was changed."; exit 1; }

# Steps answer 0 to advance, 1 to go back, 3 to skip without changing direction.
SKIP=3

ask() {  # <prompt> <default>
    (( USE_DEFAULTS )) && { printf '%s' "$2"; return 0; }
    box --inputbox "$1" 12 74 "$2"
}

ask_secret() {  # <prompt>
    (( USE_DEFAULTS )) && { printf ''; return 0; }
    box --passwordbox "$1" 11 74
}

# Back past the first screen is the only way out other than ESC, which aborts everywhere.
run_steps() {
    local -a steps=("$@")
    local i=0 rc dir=1
    while (( i < ${#steps[@]} )); do
        if "${steps[$i]}"; then rc=0; else rc=$?; fi
        case $rc in
            0) dir=1 ;;
            1) dir=-1 ;;
            "$SKIP") ;;
            *) cancelled ;;
        esac
        i=$(( i + dir ))
        if (( i < 0 )); then cancelled; fi
    done
    return 0
}

step_welcome() {
    (( USE_DEFAULTS )) && return 0
    local compose
    compose=$(compose_version)
    box --ok-button Start --msgbox \
        "This configures and starts DICOMHawk, a DICOM honeypot.\n\nDocker Compose ${compose} detected.\n\nThe service is meant to look attackable. Do not run it on a host you care about, and read docs/deployment.md before exposing it to the internet.\n\nKeyboard only, no mouse: Tab moves to the buttons, arrows choose, Space toggles, Enter confirms.\nBack returns to the previous question; Esc abandons the run." \
        19 74
}

step_profile() {
    (( USE_DEFAULTS )) && return 0
    local -a options=() names=()
    local dir name choice path rc
    # Discover profiles instead of listing them, so a new one appears here the day it is added.
    for dir in "$REPO_ROOT"/src/profiles/*/; do
        name=${dir%/}
        name=${name##*/}
        [[ -f $dir$name.yaml ]] || continue
        names+=("$name")
    done

    while :; do
        options=("none" "No profile: plain DICOM identity, no web surface" \
            "$( [[ -z $PROFILE ]] && echo ON || echo OFF )")
        for name in "${names[@]}"; do
            options+=("$name" "Impersonate the $name device" \
                "$( [[ $PROFILE == "$name" ]] && echo ON || echo OFF )")
        done
        options+=("custom" "Path to a profile YAML of your own" \
            "$( [[ -n $PROFILE && ! " ${names[*]} " =~ " $PROFILE " ]] && echo ON || echo OFF )")

        choice=$(box --radiolist \
            "Which device should the honeypot impersonate?\n\nA profile drives the advertised identity, the accepted SOP classes, and the web surface." \
            $(( ${#names[@]} + 11 )) 74 $(( ${#names[@]} + 2 )) "${options[@]}") || return $?

        case $choice in
            none) PROFILE=""; return 0 ;;
            custom)
                if path=$(ask "Absolute path to the profile YAML, as the container will see it:" "$PROFILE"); then
                    [[ -n $path ]] || continue
                    PROFILE=$path
                    return 0
                fi
                rc=$?
                (( rc == 1 )) || return $rc
                ;;
            *) PROFILE=$choice; return 0 ;;
        esac
    done
}

step_ae_title() {
    local value
    value=$(ask "AE title to advertise.\n\nLeave empty to use the profile's own. Overriding it can contradict the device you are impersonating." "$AE_TITLE") || return $?
    AE_TITLE=$value
}

step_ports() {
    local value
    while :; do
        value=$(ask "DIMSE port(s) to listen on, comma-separated.\n\n104 is the standard DICOM port and the most convincing choice." "$PORTS") || return $?
        [[ -n $value ]] || return 0
        valid_port_list "$value" && { PORTS=$value; return 0; }
        reject_port "DIMSE port list" "$value"
    done
}

step_web_port() {
    local value
    while :; do
        value=$(ask "Port for the attacker-facing web interface:" "$WEB_PORT") || return $?
        [[ -n $value ]] || return 0
        valid_port "$value" && { WEB_PORT=$value; return 0; }
        reject_port "web port" "$value"
    done
}

step_operator_port() {
    local value
    while :; do
        value=$(ask "Port for the operator API (published on host loopback only):" "$OPERATOR_PORT") || return $?
        [[ -n $value ]] || return 0
        valid_port "$value" && { OPERATOR_PORT=$value; return 0; }
        reject_port "operator port" "$value"
    done
}

step_operator_token() {
    (( USE_DEFAULTS )) && return 0
    local choice typed rc
    while :; do
        choice=$(box --menu \
            "Operator API authentication.\n\nIt is published on host loopback only, but a token also protects it from anything else running on this host." \
            15 74 3 \
            "generate" "Generate a strong random token" \
            "type" "Type my own" \
            "none" "No authentication") || return $?

        case $choice in
            generate)
                OPERATOR_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | cut -c1-32)
                return 0 ;;
            none) OPERATOR_TOKEN=""; return 0 ;;
            type)
                if typed=$(ask_secret "Operator API token:"); then
                    OPERATOR_TOKEN=$typed
                    return 0
                fi
                rc=$?
                (( rc == 1 )) || return $rc
                ;;
        esac
    done
}

step_features() {
    (( USE_DEFAULTS )) && return 0
    local chosen
    chosen=$(box --separate-output --checklist \
        "Optional components. Both are on by default and neither is visible to an attacker." \
        12 74 2 \
        "analysis" "Static analysis of captured payloads (YARA)" \
            "$( [[ $ANALYSIS == true ]] && echo ON || echo OFF )" \
        "fingerprint" "Browser fingerprinting on the web surface" \
            "$( [[ $FINGERPRINT == true ]] && echo ON || echo OFF )") || return $?

    [[ $chosen == *analysis* ]] && ANALYSIS="true" || ANALYSIS="false"
    [[ $chosen == *fingerprint* ]] && FINGERPRINT="true" || FINGERPRINT="false"
    return 0
}

step_backend_server() {
    [[ $PROFILE == "fujifilm" ]] || return "$SKIP"
    local value
    value=$(ask "X-Backendserver header value.\n\nChange it per deployment; a shared value is a fingerprint." "$BACKEND_SERVER") || return $?
    BACKEND_SERVER=$value
}

step_public_base_url() {
    local value
    value=$(ask "External HTTPS origin, if this sits behind a TLS proxy (optional):" "$PUBLIC_BASE_URL") || return $?
    PUBLIC_BASE_URL=$value
}

step_trusted_proxy() {
    local value
    value=$(ask "Exact reverse-proxy IP trusted for forwarded client identity (optional):" "$TRUSTED_PROXY") || return $?
    TRUSTED_PROXY=$value
}

step_transport() {
    (( USE_DEFAULTS )) && return 0
    local choice
    choice=$(box --menu \
        "How will attackers reach the web surface?\n\nProfiles that model an HTTPS product mark their session cookie Secure. A browser discards such a cookie over plain HTTP, so the decoy login accepts the bait credential and then silently drops the session." \
        18 74 2 \
        "http" "Plain HTTP: relax the cookie so the decoy login works" \
        "https" "Behind a TLS terminator: keep the profile's own behaviour") || return $?

    [[ $choice == http ]] && SECURE_COOKIES="false" || SECURE_COOKIES=""
    return 0
}

step_seed_choice() {
    (( USE_DEFAULTS )) && return 0
    local choice
    choice=$(box --menu \
        "Seed the database with realistic DICOM studies after starting?\n\nWithout it the honeypot answers queries from an empty database, which is itself a tell. Seeding needs outbound internet access." \
        16 74 2 \
        "seed" "Download a sample from TCIA and seed" \
        "skip" "Start with an empty database") || return $?

    [[ $choice == seed ]] && DO_SEED=1 || DO_SEED=0
    return 0
}

seeding_skipped() { (( DO_SEED )) || return "$SKIP"; }

step_seed_collection() {
    seeding_skipped || return $?
    local value
    value=$(ask "TCIA collection(s), comma-separated:" "$SEED_COLLECTION") || return $?
    [[ -n $value ]] && SEED_COLLECTION=$value
    return 0
}

step_seed_modality() {
    seeding_skipped || return $?
    local value
    value=$(ask "Modality/modalities, comma-separated:" "$SEED_MODALITY") || return $?
    [[ -n $value ]] && SEED_MODALITY=$value
    return 0
}

step_seed_max_series() {
    seeding_skipped || return $?
    local value
    value=$(ask "Maximum series to download:" "$SEED_MAX_SERIES") || return $?
    [[ -n $value ]] && SEED_MAX_SERIES=$value
    return 0
}

step_seed_max_images() {
    seeding_skipped || return $?
    local value
    value=$(ask "Images per series:" "$SEED_MAX_IMAGES") || return $?
    [[ -n $value ]] && SEED_MAX_IMAGES=$value
    return 0
}

step_seed_locale() {
    seeding_skipped || return $?
    local value
    value=$(ask "Locale for generated patient and physician names:" "$SEED_LOCALE") || return $?
    [[ -n $value ]] && SEED_LOCALE=$value
    return 0
}

step_osm_choice() {
    seeding_skipped || return $?
    (( USE_DEFAULTS )) && return 0
    local choice
    choice=$(box --menu \
        "Where should institution names come from?" 13 74 2 \
        "builtin" "The bundled list of plausible institutions" \
        "osm" "Real hospital names from OpenStreetMap") || return $?

    if [[ $choice == builtin ]]; then
        SEED_OSM_CITY=""
        SEED_OSM_COUNTRY=""
        USE_OSM=0
    else
        USE_OSM=1
    fi
    return 0
}

osm_skipped() { (( DO_SEED && USE_OSM )) || return "$SKIP"; }

step_osm_city() {
    osm_skipped || return $?
    local value
    value=$(ask "City to query:" "$SEED_OSM_CITY") || return $?
    SEED_OSM_CITY=$value
}

step_osm_country() {
    osm_skipped || return $?
    local value
    value=$(ask "ISO 3166-1 alpha-2 country code (e.g. US, DE):" "$SEED_OSM_COUNTRY") || return $?
    SEED_OSM_COUNTRY=$value
}

step_honey_url() {
    seeding_skipped || return $?
    local value
    value=$(ask "Canary URL to bake into one seeded instance (optional):" "$SEED_HONEY_URL") || return $?
    SEED_HONEY_URL=$value
}

step_canary_pdf() {
    seeding_skipped || return $?
    local value
    value=$(ask "Path to a PDF canary token, as the container sees it (optional):" "$SEED_CANARY_PDF") || return $?
    SEED_CANARY_PDF=$value
}

step_review() {
    (( USE_DEFAULTS )) && return 0
    PREVIEW_FILE=$(mktemp)
    {
        echo "Profile:            ${PROFILE:-<none, plain DICOM>}"
        echo "AE title:           ${AE_TITLE:-<profile default>}"
        echo "DIMSE ports:        $PORTS"
        echo "Web port:           $WEB_PORT"
        echo "Operator port:      $OPERATOR_PORT (127.0.0.1 only)"
        echo "Operator token:     $( [[ -n $OPERATOR_TOKEN ]] && echo '<set>' || echo '<none>' )"
        echo "Payload analysis:   $ANALYSIS"
        echo "Fingerprinting:     $FINGERPRINT"
        [[ $PROFILE == "fujifilm" ]] && echo "X-Backendserver:    $BACKEND_SERVER"
        echo "Public base URL:    ${PUBLIC_BASE_URL:-<none>}"
        echo "Trusted proxy:      ${TRUSTED_PROXY:-<none>}"
        echo
        if (( DO_SEED )); then
            echo "Seed:               $SEED_COLLECTION / $SEED_MODALITY, $SEED_MAX_SERIES series x $SEED_MAX_IMAGES images"
            echo "Seed locale:        $SEED_LOCALE"
            [[ -n $SEED_OSM_CITY ]] && echo "OSM institutions:   $SEED_OSM_CITY / $SEED_OSM_COUNTRY"
        else
            echo "Seed:               skipped"
        fi
    } >"$PREVIEW_FILE"

    whiptail --title "$TITLE" --backtitle "$BACKTITLE" \
        --yes-button "Write it" --no-button "Back" \
        --yesno "$(cat "$PREVIEW_FILE")" 24 74
}

ask_everything() {
    run_steps \
        step_welcome step_profile step_ae_title step_ports step_web_port step_operator_port \
        step_operator_token step_features step_backend_server step_public_base_url \
        step_trusted_proxy step_transport step_seed_choice step_seed_collection step_seed_modality \
        step_seed_max_series step_seed_max_images step_seed_locale step_osm_choice \
        step_osm_city step_osm_country step_honey_url step_canary_pdf step_review
}

handle_existing_env() {
    [[ -f $ENV_FILE ]] || return 0
    (( RECONFIGURE )) && return 0

    if (( USE_DEFAULTS )); then
        die "$ENV_FILE already exists. Re-run with --reconfigure to overwrite it."
    fi

    local choice
    choice=$(box --menu \
        "A configuration already exists at .env.\n\nWhat would you like to do?" 15 74 3 \
        "reconfigure" "Answer the questions again and overwrite it" \
        "keep" "Keep it and just build and start" \
        "abort" "Change nothing and exit") || cancelled

    case $choice in
        keep) KEEP_EXISTING=1 ;;
        abort) cancelled ;;
    esac
}

# ---- run ----

compose_failed() {  # <what>
    red "$1 failed."
    "${DOCKER[@]}" compose logs --tail=50 dicomhawk 2>/dev/null || true
    exit 1
}

wait_for_health() {
    local waited=0 status
    info "Waiting for the container to report healthy…"
    while (( waited < HEALTH_TIMEOUT )); do
        status=$("${DOCKER[@]}" compose ps --format '{{.Health}}' dicomhawk 2>/dev/null | head -n 1)
        case $status in
            healthy) green "Container is healthy."; return 0 ;;
            unhealthy) compose_failed "The container reported unhealthy" ;;
        esac
        sleep 3
        waited=$(( waited + 3 ))
    done
    compose_failed "The container did not become healthy within ${HEALTH_TIMEOUT}s"
}

run_seed() {
    local -a args=(
        --collection "$SEED_COLLECTION"
        --modality "$SEED_MODALITY"
        --max-series "$SEED_MAX_SERIES"
        --max-images "$SEED_MAX_IMAGES"
        --locale "$SEED_LOCALE"
    )
    [[ -n $SEED_OSM_CITY ]] && args+=(--osm-city "$SEED_OSM_CITY")
    [[ -n $SEED_OSM_COUNTRY ]] && args+=(--osm-country "$SEED_OSM_COUNTRY")
    [[ -n $SEED_HONEY_URL ]] && args+=(--honey-url "$SEED_HONEY_URL")
    [[ -n $SEED_CANARY_PDF ]] && args+=(--canary-pdf "$SEED_CANARY_PDF")

    info "Seeding the database…"
    echo "    source     $SEED_COLLECTION ($SEED_MODALITY), up to $SEED_MAX_SERIES series x $SEED_MAX_IMAGES images"
    [[ -n $SEED_OSM_CITY ]] && echo "    hospitals  OpenStreetMap: $SEED_OSM_CITY"
    echo "    This downloads from TCIA and can take several minutes. Progress is printed per series."
    # Non-fatal: an unreachable TCIA still leaves a working honeypot with the offline fallback.
    if ! "${DOCKER[@]}" compose exec -T dicomhawk dicomhawk seed "${args[@]}"; then
        red "Seeding failed. The honeypot is running; re-run 'docker compose exec dicomhawk dicomhawk seed' once the source is reachable."
    fi
}

# Asked of the container so routes come from the profile loader, not a second copy kept here.
profile_endpoints() {
    "${DOCKER[@]}" compose exec -T dicomhawk python3 -c '
import os
from profiles.profile import load_profile

profile = load_profile(os.environ.get("DICOMHAWK_PROFILE") or None)
print(f"name\t{profile.name}")
print(f"ae_title\t{profile.ae_title}")
web = getattr(profile, "web", None)
if web is not None and getattr(web, "enabled", False):
    for key in ("entry", "login", "worklist", "console"):
        path = (web.routes or {}).get(key)
        if path:
            print(f"web\t{key}\t{path}")
dicomweb = getattr(profile, "dicomweb", None)
if dicomweb is not None and getattr(dicomweb, "enabled", False):
    for service in dicomweb.services:
        print(f"dicomweb\t{service.kind}\t{service.port}\t{service.base_path}")
' 2>/dev/null || true
}

summary() {
    local port kind key path name ae endpoints
    endpoints=$(profile_endpoints)
    name=$(printf '%s\n' "$endpoints" | awk -F'\t' '$1=="name"{print $2; exit}')
    # The container knows its own AE title; only fall back if it could not be asked.
    ae=${AE_TITLE:-$(printf '%s\n' "$endpoints" | awk -F'\t' '$1=="ae_title"{print $2; exit}')}

    green ""
    green "DICOMHawk is running."
    echo
    echo "  Profile          ${PROFILE:-<none, plain DICOM>}${name:+  ($name)}"
    for port in ${PORTS//,/ }; do
        echo "  DIMSE            $port          AE title: ${ae:-<the profile default>}"
    done

    if [[ -n $endpoints ]]; then
        while IFS=$'\t' read -r kind key path; do
            [[ $kind == web ]] || continue
            printf '  %-16s http://localhost:%s%s\n' "web: $key" "$WEB_PORT" "$path"
        done <<<"$endpoints"

        while IFS=$'\t' read -r kind key port path; do
            [[ $kind == dicomweb ]] || continue
            printf '  %-16s http://localhost:%s%s\n' "dicomweb: $key" "$port" "$path"
        done <<<"$endpoints"
    else
        echo "  Web surface      http://localhost:$WEB_PORT"
    fi

    echo
    echo "  Operator API     http://127.0.0.1:$OPERATOR_PORT   (loopback only)"
    if [[ -n $OPERATOR_TOKEN ]]; then
        echo "    token          $OPERATOR_TOKEN"
        # Flask answers with a Basic challenge, but only the password is checked.
        echo "    in a browser   leave the username blank, paste the token as the password"
        echo "    with curl      curl -H 'Authorization: Bearer <token>' http://127.0.0.1:$OPERATOR_PORT/api/stats"
        echo "    lost it?       grep DICOMHAWK_OPERATOR_TOKEN .env"
    else
        red "    no token set: anything able to reach loopback can read captured intelligence"
    fi

    echo
    if [[ $SECURE_COOKIES == false ]]; then
        echo "  Decoy login      reachable over plain HTTP (session cookie not marked Secure)"
    else
        echo "  Decoy login      needs TLS in front; over plain HTTP the browser drops the session"
    fi
    echo "  Payload analysis $ANALYSIS"
    echo "  Fingerprinting   $FINGERPRINT"
    echo "  Seeded data      $( (( DO_SEED )) && echo yes || echo "no, and an empty database is itself a tell" )"
    echo
    echo "  Logs             docker compose logs -f dicomhawk"
    echo "  Stop             docker compose down"
    echo "  Re-seed          docker compose exec dicomhawk dicomhawk seed"
    echo "  Configuration    .env"
    echo
    echo "Before exposing this to the internet, work through docs/deployment.md. Egress"
    echo "lockdown and storage quotas are host-level steps this script deliberately leaves alone."
}

main() {
    install_prerequisites
    check_prerequisites
    KEEP_EXISTING=0
    handle_existing_env

    if (( ! KEEP_EXISTING )); then
        ask_everything
        if [[ -z $SECURE_COOKIES && -z $TRUSTED_PROXY && $PUBLIC_BASE_URL != https://* ]]; then
            SECURE_COOKIES="false"
        fi
        if ports_collide; then
            die "The same host port was chosen more than once; DIMSE, web, and operator ports must differ."
        fi
        warn_busy_ports
        write_env
        write_override
    fi

    if (( ! DO_START )); then
        green "Configuration written. Start it with: docker compose up -d"
        return 0
    fi

    info "Building the image…"
    "${DOCKER[@]}" compose build || compose_failed "Build"

    info "Starting the stack…"
    "${DOCKER[@]}" compose up -d || compose_failed "Startup"

    wait_for_health
    (( DO_SEED )) && run_seed
    summary
}

main "$@"

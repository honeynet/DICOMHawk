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
DEFAULT_DATA_DIR="${HOME}/data/dicomhawk"
DEFAULT_HONEY_URL="https://example.com/honey"

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
cleanup() { rm -f "$REPO_ROOT"/.env.tmp.* "$REPO_ROOT"/.compose.tmp.* "$PREVIEW_FILE" "$SOURCES_FILE" 2>/dev/null || true; }
trap cleanup EXIT

red() { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
info() { printf '\033[0;34m==>\033[0m %s\n' "$*"; }
die() { red "$*"; exit 1; }

# Fit dialogs to the terminal; COLUMNS is usually set, tput covers shells that omit it.
dialog_width() {
    local columns=${COLUMNS:-}
    if [[ ! $columns =~ ^[0-9]+$ ]] && command -v tput >/dev/null 2>&1; then
        columns=$(tput cols 2>/dev/null || true)
    fi
    [[ $columns =~ ^[0-9]+$ ]] || columns=120
    columns=$(( columns > 4 ? columns - 4 : columns ))
    (( columns > 116 )) && columns=116
    (( columns < 40 )) && columns=40
    printf '%s' "$columns"
}

DIALOG_WIDTH=$(dialog_width)

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
DATA_DIR=$(answer DICOMHAWK_DATA_DIR "$DEFAULT_DATA_DIR")
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

SEED_COLLECTION=$(answer DICOMHAWK_SEED_COLLECTION "TCGA-LUAD")
SEED_MODALITY=$(answer DICOMHAWK_SEED_MODALITY "CT")
SEED_MAX_SERIES=$(answer DICOMHAWK_SEED_MAX_SERIES "3")
SEED_MAX_IMAGES=$(answer DICOMHAWK_SEED_MAX_IMAGES "30")
SEED_LOCALE=$(answer DICOMHAWK_SEED_LOCALE "en_US")
SEED_OSM_CITY=$(answer DICOMHAWK_SEED_OSM_CITY "")
SEED_OSM_COUNTRY=$(answer DICOMHAWK_SEED_OSM_COUNTRY "")
SEED_HONEY_URL=$(answer DICOMHAWK_SEED_HONEY_URL "$DEFAULT_HONEY_URL")
SEED_CANARY_PDF=$(answer DICOMHAWK_SEED_CANARY_PDF "")
USE_OSM=0
CUSTOM_PROFILE=0
CUSTOM_PROFILE_NAME=$(answer DICOMHAWK_CUSTOM_PROFILE_NAME "custom-pacs")
CUSTOM_IMPLEMENTATION_UID=$(answer DICOMHAWK_CUSTOM_IMPLEMENTATION_UID "1.2.826.0.1.3680043.9.3811.2.0.1")
CUSTOM_IMPLEMENTATION_VERSION=$(answer DICOMHAWK_CUSTOM_IMPLEMENTATION_VERSION "ORTHANC")
CUSTOM_MANUFACTURER=$(answer DICOMHAWK_CUSTOM_MANUFACTURER "Orthanc")
CUSTOM_MODEL=$(answer DICOMHAWK_CUSTOM_MODEL "Generic PACS")
CUSTOM_OPERATIONS=$(answer DICOMHAWK_CUSTOM_OPERATIONS "echo,find,get,move,store")
CUSTOM_MAX_ASSOCIATIONS=$(answer DICOMHAWK_CUSTOM_MAX_ASSOCIATIONS "16")
CUSTOM_MAX_PDU_SIZE=$(answer DICOMHAWK_CUSTOM_MAX_PDU_SIZE "65536")
CUSTOM_ACSE_TIMEOUT=$(answer DICOMHAWK_CUSTOM_ACSE_TIMEOUT "10")
CUSTOM_NETWORK_TIMEOUT=$(answer DICOMHAWK_CUSTOM_NETWORK_TIMEOUT "15")
CUSTOM_DIMSE_TIMEOUT=$(answer DICOMHAWK_CUSTOM_DIMSE_TIMEOUT "20")
CUSTOM_MAX_STORE_BYTES=$(answer DICOMHAWK_CUSTOM_MAX_STORE_BYTES "67108864")
CUSTOM_REQUIRE_CALLED_AET=$(answer DICOMHAWK_CUSTOM_REQUIRE_CALLED_AET "false")
CUSTOM_REQUIRE_CALLING_AETS=$(answer DICOMHAWK_CUSTOM_REQUIRE_CALLING_AETS "")
CUSTOM_WEB_ENABLED=$(answer DICOMHAWK_CUSTOM_WEB_ENABLED "true")
CUSTOM_WEB_BROWSE=$(answer DICOMHAWK_CUSTOM_WEB_BROWSE "true")
CUSTOM_WEB_GRANT_ACCESS=$(answer DICOMHAWK_CUSTOM_WEB_GRANT_ACCESS "keyword")
CUSTOM_WEB_SECURE_COOKIES=$(answer DICOMHAWK_CUSTOM_WEB_SECURE_COOKIES "false")
CUSTOM_HONEY_USERNAME=$(answer DICOMHAWK_CUSTOM_HONEY_USERNAME "test")
CUSTOM_HONEY_PASSWORD=$(answer DICOMHAWK_CUSTOM_HONEY_PASSWORD "test")
CUSTOM_HONEY_KEYWORDS=$(answer DICOMHAWK_CUSTOM_HONEY_KEYWORDS "admin,pacs,dicom,radiology,imaging,service")
CUSTOM_WEB_SERVER=$(answer DICOMHAWK_CUSTOM_WEB_SERVER "Apache")
CUSTOM_WEB_SITE_NAME=$(answer DICOMHAWK_CUSTOM_WEB_SITE_NAME "Generic PACS")
CUSTOM_WEB_VERSION=$(answer DICOMHAWK_CUSTOM_WEB_VERSION "1.0")
CUSTOM_WEB_ROUTE_PREFIX=$(answer DICOMHAWK_CUSTOM_WEB_ROUTE_PREFIX "/portal")
CUSTOM_WEB_COOKIE_PREFIX=$(answer DICOMHAWK_CUSTOM_WEB_COOKIE_PREFIX "portal")
CUSTOM_WEB_MAX_REQUEST_BYTES=$(answer DICOMHAWK_CUSTOM_WEB_MAX_REQUEST_BYTES "1048576")
CUSTOM_UPLOAD_MAX_REQUEST_BYTES=$(answer DICOMHAWK_CUSTOM_UPLOAD_MAX_REQUEST_BYTES "52428800")
CUSTOM_UPLOAD_MAX_FILES=$(answer DICOMHAWK_CUSTOM_UPLOAD_MAX_FILES "10")
CUSTOM_PAGE_SIZE=$(answer DICOMHAWK_CUSTOM_PAGE_SIZE "100")
CUSTOM_FINGERPRINT_ENABLED=$(answer DICOMHAWK_CUSTOM_FINGERPRINT_ENABLED "true")
CUSTOM_FINGERPRINT_SIGNALS=$(answer DICOMHAWK_CUSTOM_FINGERPRINT_SIGNALS "browser,rendering,math,screen,bot")
CUSTOM_DICOMWEB_ENABLED=$(answer DICOMHAWK_CUSTOM_DICOMWEB_ENABLED "true")
CUSTOM_DICOMWEB_SERVICES=$(answer DICOMHAWK_CUSTOM_DICOMWEB_SERVICES "qido,wado_rs,stow")
CUSTOM_DICOMWEB_PORT=$(answer DICOMHAWK_CUSTOM_DICOMWEB_PORT "8042")
CUSTOM_DICOMWEB_BASE_PATH=$(answer DICOMHAWK_CUSTOM_DICOMWEB_BASE_PATH "/dicom-web")
CUSTOM_DICOMWEB_REQUIRE_AUTH=$(answer DICOMHAWK_CUSTOM_DICOMWEB_REQUIRE_AUTH "")
CUSTOM_DICOMWEB_QIDO_MAX_RESULTS=$(answer DICOMHAWK_CUSTOM_DICOMWEB_QIDO_MAX_RESULTS "20000")
CUSTOM_DICOMWEB_MAX_REQUEST_BYTES=$(answer DICOMHAWK_CUSTOM_DICOMWEB_MAX_REQUEST_BYTES "67108864")
CUSTOM_DICOMWEB_MAX_STOW_PARTS=$(answer DICOMHAWK_CUSTOM_DICOMWEB_MAX_STOW_PARTS "128")
CUSTOM_DICOMWEB_MEDIA_TYPE=$(answer DICOMHAWK_CUSTOM_DICOMWEB_MEDIA_TYPE "application/dicom+json")
CUSTOM_DICOMWEB_TRANSFER_SYNTAX=$(answer DICOMHAWK_CUSTOM_DICOMWEB_TRANSFER_SYNTAX "1.2.840.10008.1.2.1")
CUSTOM_DICOMWEB_AUTH_SCHEMES=$(answer DICOMHAWK_CUSTOM_DICOMWEB_AUTH_SCHEMES "Basic")
[[ $PROFILE == /opt/dicomhawk/profiles/custom.yaml ]] && CUSTOM_PROFILE=1

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

valid_positive_integer() { [[ $1 =~ ^[1-9][0-9]*$ ]]; }
valid_positive_number() {
    local nonzero=${1//[0.]/}
    [[ $1 =~ ^([0-9]+)(\.[0-9]+)?$ && -n $nonzero ]]
}
valid_boolean() { [[ $1 == true || $1 == false ]]; }
valid_ae_title() { [[ -z $1 || ( ${#1} -le 16 && $1 =~ ^[[:print:]]+$ && $1 != *\\* ) ]]; }
valid_dicom_uid() { [[ $1 =~ ^[0-9]+(\.[0-9]+)+$ && ${#1} -le 64 ]]; }
valid_url() { [[ -z $1 || $1 =~ ^https?://[^[:space:]]+$ ]]; }
valid_url_path() { [[ $1 == /* && $1 != //* && $1 != *'?'* && $1 != *'#'* && $1 != *\\* && $1 != *[[:space:]]* ]]; }
csv_subset() {  # <comma-list> <space-separated-allowlist> [allow-empty]
    local input=$1 allowed=$2 allow_empty=${3:-0} item
    [[ -n $input ]] || (( allow_empty ))
    [[ -n $input ]] || return 0
    for item in ${input//,/ }; do
        [[ " $allowed " == *" $item "* ]] || return 1
    done
}
valid_ip() {
    [[ -z $1 ]] && return 0
    if [[ $1 == *:* ]]; then
        [[ ${#1} -le 39 && $1 =~ ^[0-9A-Fa-f:]+$ && $1 =~ [0-9A-Fa-f] && $1 != *:::* ]]
        return
    fi
    local octet count=0
    for octet in ${1//./ }; do
        [[ $octet =~ ^[0-9]+$ ]] && (( octet <= 255 )) || return 1
        count=$(( count + 1 ))
    done
    (( count == 4 ))
}

valid_profile() {
    [[ -z $PROFILE ]] && return 0
    [[ $PROFILE == /opt/dicomhawk/profiles/custom.yaml ]] && return 0
    [[ $PROFILE != */* && -f $REPO_ROOT/src/profiles/$PROFILE/$PROFILE.yaml ]]
}

override_is_safe() {
    [[ ! -e $OVERRIDE_FILE ]] ||
        [[ $(head -n 1 "$OVERRIDE_FILE") == "# Generated by setup.sh. Do not edit; re-run the script instead." ]]
}

validate_answers() {
    valid_profile || die "Profile '$PROFILE' is neither packaged nor generated by this installer."
    [[ $DATA_DIR == /* ]] || die "DICOMHAWK_DATA_DIR must be an absolute path."
    valid_ae_title "$AE_TITLE" || die "AE title must be at most 16 printable characters and cannot contain a backslash."
    valid_port_list "$PORTS" || reject_port "DIMSE port list" "$PORTS"
    valid_port "$WEB_PORT" || reject_port "web port" "$WEB_PORT"
    valid_port "$OPERATOR_PORT" || reject_port "operator port" "$OPERATOR_PORT"
    valid_boolean "$ANALYSIS" || die "DICOMHAWK_ANALYSIS must be true or false."
    valid_boolean "$FINGERPRINT" || die "DICOMHAWK_FINGERPRINT must be true or false."
    valid_url "$PUBLIC_BASE_URL" || die "Public base URL must start with http:// or https:// and contain no spaces."
    valid_ip "$TRUSTED_PROXY" || die "Trusted proxy must be one exact IPv4 or IPv6 address."
    valid_positive_integer "$SEED_MAX_SERIES" || die "Maximum series must be a positive integer."
    valid_positive_integer "$SEED_MAX_IMAGES" || die "Images per series must be a positive integer."
    [[ $SEED_LOCALE =~ ^[A-Za-z]{2,3}(_[A-Za-z]{2})?$ ]] || die "Seed locale must look like en_US."
    [[ -z $SEED_OSM_COUNTRY || $SEED_OSM_COUNTRY =~ ^[A-Za-z]{2}$ ]] || die "OSM country must be a two-letter code."
    if (( CUSTOM_PROFILE )); then
        [[ $CUSTOM_PROFILE_NAME =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || die "Custom profile name may contain letters, digits, underscores, and hyphens."
        valid_ae_title "$AE_TITLE" && [[ -n $AE_TITLE ]] || die "A custom profile requires an AE title."
        valid_dicom_uid "$CUSTOM_IMPLEMENTATION_UID" || die "Implementation Class UID must be a numeric dotted DICOM UID of at most 64 characters."
        [[ -n $CUSTOM_IMPLEMENTATION_VERSION && ${#CUSTOM_IMPLEMENTATION_VERSION} -le 16 ]] || die "Implementation Version Name must contain 1-16 characters."
        csv_subset "$CUSTOM_OPERATIONS" "echo find get move store" || die "Custom DICOM operations must be selected from echo, find, get, move, and store."
        for value in "$CUSTOM_MAX_ASSOCIATIONS" "$CUSTOM_MAX_PDU_SIZE" "$CUSTOM_MAX_STORE_BYTES" \
            "$CUSTOM_WEB_MAX_REQUEST_BYTES" "$CUSTOM_UPLOAD_MAX_REQUEST_BYTES" "$CUSTOM_UPLOAD_MAX_FILES" \
            "$CUSTOM_PAGE_SIZE" "$CUSTOM_DICOMWEB_QIDO_MAX_RESULTS" "$CUSTOM_DICOMWEB_MAX_REQUEST_BYTES" \
            "$CUSTOM_DICOMWEB_MAX_STOW_PARTS"; do
            valid_positive_integer "$value" || die "Custom profile size, count, and port limits must be positive integers."
        done
        for value in "$CUSTOM_ACSE_TIMEOUT" "$CUSTOM_NETWORK_TIMEOUT" "$CUSTOM_DIMSE_TIMEOUT"; do
            valid_positive_number "$value" || die "Custom profile timeouts must be positive numbers."
        done
        valid_boolean "$CUSTOM_REQUIRE_CALLED_AET" || die "Custom require-called-AET must be true or false."
        for value in "$CUSTOM_WEB_ENABLED" "$CUSTOM_WEB_BROWSE" "$CUSTOM_WEB_SECURE_COOKIES" \
            "$CUSTOM_FINGERPRINT_ENABLED" "$CUSTOM_DICOMWEB_ENABLED"; do
            valid_boolean "$value" || die "Custom profile feature switches must be true or false."
        done
        [[ $CUSTOM_WEB_GRANT_ACCESS =~ ^(none|bait|keyword|any)$ ]] || die "Custom web access must be none, bait, keyword, or any."
        valid_url_path "$CUSTOM_WEB_ROUTE_PREFIX" && [[ $CUSTOM_WEB_ROUTE_PREFIX != / && $CUSTOM_WEB_ROUTE_PREFIX != */ ]] || die "Custom web route prefix must look like /portal (not / and no trailing slash)."
        [[ $CUSTOM_WEB_COOKIE_PREFIX =~ ^[A-Za-z0-9_.-]+$ ]] || die "Custom cookie prefix may contain letters, digits, dots, underscores, and hyphens."
        if [[ $CUSTOM_FINGERPRINT_ENABLED == true ]]; then
            csv_subset "$CUSTOM_FINGERPRINT_SIGNALS" "browser rendering math screen bot" || die "Unknown custom fingerprint signal."
        else
            csv_subset "$CUSTOM_FINGERPRINT_SIGNALS" "browser rendering math screen bot" 1 || die "Unknown custom fingerprint signal."
        fi
        if [[ $CUSTOM_DICOMWEB_ENABLED == true ]]; then
            csv_subset "$CUSTOM_DICOMWEB_SERVICES" "qido wado_rs stow wado_uri" || die "Unknown custom DICOMweb service."
        else
            csv_subset "$CUSTOM_DICOMWEB_SERVICES" "qido wado_rs stow wado_uri" 1 || die "Unknown custom DICOMweb service."
        fi
        valid_port "$CUSTOM_DICOMWEB_PORT" || reject_port "custom DICOMweb port" "$CUSTOM_DICOMWEB_PORT"
        valid_url_path "$CUSTOM_DICOMWEB_BASE_PATH" || die "Custom DICOMweb base path must look like /dicom-web."
        csv_subset "$CUSTOM_DICOMWEB_REQUIRE_AUTH" "qido wado_rs stow wado_uri" 1 || die "Unknown custom DICOMweb authentication service."
        local item
        for item in ${CUSTOM_DICOMWEB_REQUIRE_AUTH//,/ }; do
            [[ ",${CUSTOM_DICOMWEB_SERVICES}," == *",$item,"* ]] || die "Custom DICOMweb authentication can name only enabled services."
        done
        csv_subset "$CUSTOM_DICOMWEB_AUTH_SCHEMES" "Basic Negotiate NTLM" || die "Custom DICOMweb authentication schemes must be Basic, Negotiate, or NTLM."
        [[ $CUSTOM_DICOMWEB_MEDIA_TYPE == application/json || $CUSTOM_DICOMWEB_MEDIA_TYPE == application/dicom+json ]] || die "Custom QIDO media type must be application/json or application/dicom+json."
        valid_dicom_uid "$CUSTOM_DICOMWEB_TRANSFER_SYNTAX" || die "Custom DICOMweb transfer syntax must be a DICOM UID."
    fi
    [[ -z $SEED_CANARY_PDF || -f $SEED_CANARY_PDF ]] || die "Canary PDF '$SEED_CANARY_PDF' is not a readable host file."
    override_is_safe || die "$OVERRIDE_FILE already exists and was not generated by setup.sh; refusing to change it."
}

# Rejected up front: a bad port only surfaces as an opaque `compose up` failure minutes later.
reject_port() {  # <label> <value>
    local message="$2 is not a usable $1. Give a number between 1 and 65535"
    [[ $1 == "DIMSE port list" ]] && message+=", or several separated by commas, with no repeats"
    if (( USE_DEFAULTS )); then
        die "$message."
    fi
    box --ok-button Retry --msgbox "$message." 10 "$DIALOG_WIDTH" || true
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
        [DICOMHAWK_DATA_DIR]="$DATA_DIR"
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
        [DICOMHAWK_SEED_COLLECTION]="$SEED_COLLECTION"
        [DICOMHAWK_SEED_MODALITY]="$SEED_MODALITY"
        [DICOMHAWK_SEED_MAX_SERIES]="$SEED_MAX_SERIES"
        [DICOMHAWK_SEED_MAX_IMAGES]="$SEED_MAX_IMAGES"
        [DICOMHAWK_SEED_LOCALE]="$SEED_LOCALE"
        [DICOMHAWK_SEED_OSM_CITY]="$SEED_OSM_CITY"
        [DICOMHAWK_SEED_OSM_COUNTRY]="$SEED_OSM_COUNTRY"
        [DICOMHAWK_SEED_HONEY_URL]="$SEED_HONEY_URL"
        [DICOMHAWK_SEED_CANARY_PDF]="$SEED_CANARY_PDF"
        [DICOMHAWK_CUSTOM_PROFILE_NAME]="$CUSTOM_PROFILE_NAME"
        [DICOMHAWK_CUSTOM_IMPLEMENTATION_UID]="$CUSTOM_IMPLEMENTATION_UID"
        [DICOMHAWK_CUSTOM_IMPLEMENTATION_VERSION]="$CUSTOM_IMPLEMENTATION_VERSION"
        [DICOMHAWK_CUSTOM_MANUFACTURER]="$CUSTOM_MANUFACTURER"
        [DICOMHAWK_CUSTOM_MODEL]="$CUSTOM_MODEL"
        [DICOMHAWK_CUSTOM_OPERATIONS]="$CUSTOM_OPERATIONS"
        [DICOMHAWK_CUSTOM_MAX_ASSOCIATIONS]="$CUSTOM_MAX_ASSOCIATIONS"
        [DICOMHAWK_CUSTOM_MAX_PDU_SIZE]="$CUSTOM_MAX_PDU_SIZE"
        [DICOMHAWK_CUSTOM_ACSE_TIMEOUT]="$CUSTOM_ACSE_TIMEOUT"
        [DICOMHAWK_CUSTOM_NETWORK_TIMEOUT]="$CUSTOM_NETWORK_TIMEOUT"
        [DICOMHAWK_CUSTOM_DIMSE_TIMEOUT]="$CUSTOM_DIMSE_TIMEOUT"
        [DICOMHAWK_CUSTOM_MAX_STORE_BYTES]="$CUSTOM_MAX_STORE_BYTES"
        [DICOMHAWK_CUSTOM_REQUIRE_CALLED_AET]="$CUSTOM_REQUIRE_CALLED_AET"
        [DICOMHAWK_CUSTOM_REQUIRE_CALLING_AETS]="$CUSTOM_REQUIRE_CALLING_AETS"
        [DICOMHAWK_CUSTOM_WEB_ENABLED]="$CUSTOM_WEB_ENABLED"
        [DICOMHAWK_CUSTOM_WEB_BROWSE]="$CUSTOM_WEB_BROWSE"
        [DICOMHAWK_CUSTOM_WEB_GRANT_ACCESS]="$CUSTOM_WEB_GRANT_ACCESS"
        [DICOMHAWK_CUSTOM_WEB_SECURE_COOKIES]="$CUSTOM_WEB_SECURE_COOKIES"
        [DICOMHAWK_CUSTOM_HONEY_USERNAME]="$CUSTOM_HONEY_USERNAME"
        [DICOMHAWK_CUSTOM_HONEY_PASSWORD]="$CUSTOM_HONEY_PASSWORD"
        [DICOMHAWK_CUSTOM_HONEY_KEYWORDS]="$CUSTOM_HONEY_KEYWORDS"
        [DICOMHAWK_CUSTOM_WEB_SERVER]="$CUSTOM_WEB_SERVER"
        [DICOMHAWK_CUSTOM_WEB_SITE_NAME]="$CUSTOM_WEB_SITE_NAME"
        [DICOMHAWK_CUSTOM_WEB_VERSION]="$CUSTOM_WEB_VERSION"
        [DICOMHAWK_CUSTOM_WEB_ROUTE_PREFIX]="$CUSTOM_WEB_ROUTE_PREFIX"
        [DICOMHAWK_CUSTOM_WEB_COOKIE_PREFIX]="$CUSTOM_WEB_COOKIE_PREFIX"
        [DICOMHAWK_CUSTOM_WEB_MAX_REQUEST_BYTES]="$CUSTOM_WEB_MAX_REQUEST_BYTES"
        [DICOMHAWK_CUSTOM_UPLOAD_MAX_REQUEST_BYTES]="$CUSTOM_UPLOAD_MAX_REQUEST_BYTES"
        [DICOMHAWK_CUSTOM_UPLOAD_MAX_FILES]="$CUSTOM_UPLOAD_MAX_FILES"
        [DICOMHAWK_CUSTOM_PAGE_SIZE]="$CUSTOM_PAGE_SIZE"
        [DICOMHAWK_CUSTOM_FINGERPRINT_ENABLED]="$CUSTOM_FINGERPRINT_ENABLED"
        [DICOMHAWK_CUSTOM_FINGERPRINT_SIGNALS]="$CUSTOM_FINGERPRINT_SIGNALS"
        [DICOMHAWK_CUSTOM_DICOMWEB_ENABLED]="$CUSTOM_DICOMWEB_ENABLED"
        [DICOMHAWK_CUSTOM_DICOMWEB_SERVICES]="$CUSTOM_DICOMWEB_SERVICES"
        [DICOMHAWK_CUSTOM_DICOMWEB_PORT]="$CUSTOM_DICOMWEB_PORT"
        [DICOMHAWK_CUSTOM_DICOMWEB_BASE_PATH]="$CUSTOM_DICOMWEB_BASE_PATH"
        [DICOMHAWK_CUSTOM_DICOMWEB_REQUIRE_AUTH]="$CUSTOM_DICOMWEB_REQUIRE_AUTH"
        [DICOMHAWK_CUSTOM_DICOMWEB_QIDO_MAX_RESULTS]="$CUSTOM_DICOMWEB_QIDO_MAX_RESULTS"
        [DICOMHAWK_CUSTOM_DICOMWEB_MAX_REQUEST_BYTES]="$CUSTOM_DICOMWEB_MAX_REQUEST_BYTES"
        [DICOMHAWK_CUSTOM_DICOMWEB_MAX_STOW_PARTS]="$CUSTOM_DICOMWEB_MAX_STOW_PARTS"
        [DICOMHAWK_CUSTOM_DICOMWEB_MEDIA_TYPE]="$CUSTOM_DICOMWEB_MEDIA_TYPE"
        [DICOMHAWK_CUSTOM_DICOMWEB_TRANSFER_SYNTAX]="$CUSTOM_DICOMWEB_TRANSFER_SYNTAX"
        [DICOMHAWK_CUSTOM_DICOMWEB_AUTH_SCHEMES]="$CUSTOM_DICOMWEB_AUTH_SCHEMES"
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

yaml_quote() { printf "'%s'" "${1//\'/\'\'}"; }
yaml_inline_list() {
    local input=$1 item first=1
    printf '['
    for item in ${input//,/ }; do
        (( first )) || printf ', '
        yaml_quote "$item"
        first=0
    done
    printf ']'
}

prepare_data_layout() {
    mkdir -p "$DATA_DIR/logs" "$DATA_DIR/profiles"
    # uid/gid 999 writes rotations and lock files; 0755 keeps the host able to read them.
    if (( EUID == 0 )); then
        chown 999:999 "$DATA_DIR/logs"
        chmod 0755 "$DATA_DIR/logs"
    else
        sudo chown 999:999 "$DATA_DIR/logs"
        sudo chmod 0755 "$DATA_DIR/logs"
    fi
}

write_custom_profile() {
    (( CUSTOM_PROFILE )) || return 0
    local target="$DATA_DIR/profiles/custom.yaml" tmp route service
    tmp=$(mktemp "$DATA_DIR/profiles/.custom.tmp.XXXXXX")
    {
        echo "# Generated by setup.sh from the generic-pacs profile."
        echo "meta:"
        printf '  name: %s\n' "$(yaml_quote "$CUSTOM_PROFILE_NAME")"
        echo "  kind: pacs"
        echo "identity:"
        printf '  ae_title: %s\n' "$(yaml_quote "$AE_TITLE")"
        printf '  implementation_class_uid: %s\n' "$(yaml_quote "$CUSTOM_IMPLEMENTATION_UID")"
        printf '  implementation_version_name: %s\n' "$(yaml_quote "$CUSTOM_IMPLEMENTATION_VERSION")"
        printf '  manufacturer: %s\n' "$(yaml_quote "$CUSTOM_MANUFACTURER")"
        printf '  model_name: %s\n' "$(yaml_quote "$CUSTOM_MODEL")"
        echo "dicom:"
        printf '  operations: %s\n' "$(yaml_inline_list "$CUSTOM_OPERATIONS")"
        printf '  max_associations: %s\n' "$CUSTOM_MAX_ASSOCIATIONS"
        printf '  max_pdu_size: %s\n' "$CUSTOM_MAX_PDU_SIZE"
        printf '  acse_timeout: %s\n' "$CUSTOM_ACSE_TIMEOUT"
        printf '  network_timeout: %s\n' "$CUSTOM_NETWORK_TIMEOUT"
        printf '  dimse_timeout: %s\n' "$CUSTOM_DIMSE_TIMEOUT"
        printf '  max_store_bytes: %s\n' "$CUSTOM_MAX_STORE_BYTES"
        echo "  ae_auth:"
        printf '    require_called_aet: %s\n' "$CUSTOM_REQUIRE_CALLED_AET"
        if [[ -n $CUSTOM_REQUIRE_CALLING_AETS ]]; then
            printf '    require_calling_aet: %s\n' "$(yaml_inline_list "$CUSTOM_REQUIRE_CALLING_AETS")"
        else
            echo "    require_calling_aet: null"
        fi
        echo "  # SOP classes and transfer syntaxes use the generic catalog; edit to match a vendor."
        echo "web:"
        printf '  enabled: %s\n' "$CUSTOM_WEB_ENABLED"
        echo "  templates_dir: generic-pacs"
        printf '  browse: %s\n' "$CUSTOM_WEB_BROWSE"
        printf '  grant_access: %s\n' "$CUSTOM_WEB_GRANT_ACCESS"
        printf '  secure_cookies: %s\n' "$CUSTOM_WEB_SECURE_COOKIES"
        printf '  max_request_bytes: %s\n' "$CUSTOM_WEB_MAX_REQUEST_BYTES"
        printf '  upload_max_request_bytes: %s\n' "$CUSTOM_UPLOAD_MAX_REQUEST_BYTES"
        printf '  upload_max_files: %s\n' "$CUSTOM_UPLOAD_MAX_FILES"
        printf '  browse_page_size: %s\n' "$CUSTOM_PAGE_SIZE"
        printf '  worklist_page_size: %s\n' "$CUSTOM_PAGE_SIZE"
        echo "  headers:"
        printf '    Server: %s\n' "$(yaml_quote "$CUSTOM_WEB_SERVER")"
        echo "  identity:"
        printf '    version: %s\n' "$(yaml_quote "$CUSTOM_WEB_VERSION")"
        printf '    site_name: %s\n' "$(yaml_quote "$CUSTOM_WEB_SITE_NAME")"
        echo "  routes:"
        printf '    entry: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX")"
        printf '    worklist: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/worklist")"
        printf '    login: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/login")"
        printf '    winauth: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/winauth")"
        printf '    forgot_password: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/forgot-password")"
        printf '    sts_error: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/error")"
        printf '    sts_authorize: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/authorize")"
        printf '    csp_report: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/csp-report")"
        printf '    translated_items: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/translations")"
        for route in console patients studies series instances search upload logout; do
            printf '    %s: %s\n' "$route" "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/$route")"
        done
        printf '    fingerprint_script: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/static/telemetry.js")"
        printf '    fingerprint_ingest: %s\n' "$(yaml_quote "$CUSTOM_WEB_ROUTE_PREFIX/telemetry")"
        echo "  cookies:"
        printf '    antiforgery: %s\n' "$(yaml_quote "$CUSTOM_WEB_COOKIE_PREFIX.xsrf")"
        printf '    session: %s\n' "$(yaml_quote "${CUSTOM_WEB_COOKIE_PREFIX}_authed")"
        printf '    signin_message_prefix: %s\n' "$(yaml_quote "${CUSTOM_WEB_COOKIE_PREFIX}SignIn.")"
        printf '    nonce_prefix: %s\n' "$(yaml_quote "${CUSTOM_WEB_COOKIE_PREFIX}Nonce.")"
        printf '    idp: %s\n' "$(yaml_quote "${CUSTOM_WEB_COOKIE_PREFIX}Idp")"
        printf '    idp_token: %s\n' "$(yaml_quote "${CUSTOM_WEB_COOKIE_PREFIX}IdpToken")"
        printf '    winlogin_origurl: %s\n' "$(yaml_quote "${CUSTOM_WEB_COOKIE_PREFIX}WinOrigUrl")"
        echo "  honey_credentials:"
        printf '    - username: %s\n' "$(yaml_quote "$CUSTOM_HONEY_USERNAME")"
        printf '      password: %s\n' "$(yaml_quote "$CUSTOM_HONEY_PASSWORD")"
        printf '  honey_keywords: %s\n' "$(yaml_inline_list "$CUSTOM_HONEY_KEYWORDS")"
        echo "  fingerprint:"
        printf '    enabled: %s\n' "$CUSTOM_FINGERPRINT_ENABLED"
        printf '    signals: %s\n' "$(yaml_inline_list "$CUSTOM_FINGERPRINT_SIGNALS")"
        echo "dicomweb:"
        printf '  enabled: %s\n' "$CUSTOM_DICOMWEB_ENABLED"
        if [[ -n $CUSTOM_DICOMWEB_SERVICES ]]; then
            echo "  services:"
            for service in ${CUSTOM_DICOMWEB_SERVICES//,/ }; do
                printf '    - service: %s\n' "$service"
                printf '      base_path: %s\n' "$(yaml_quote "$CUSTOM_DICOMWEB_BASE_PATH")"
                printf '      port: %s\n' "$CUSTOM_DICOMWEB_PORT"
            done
        else
            echo "  services: []"
        fi
        printf '  require_auth: %s\n' "$(yaml_inline_list "$CUSTOM_DICOMWEB_REQUIRE_AUTH")"
        printf '  qido_max_results: %s\n' "$CUSTOM_DICOMWEB_QIDO_MAX_RESULTS"
        printf '  max_request_bytes: %s\n' "$CUSTOM_DICOMWEB_MAX_REQUEST_BYTES"
        printf '  max_stow_parts: %s\n' "$CUSTOM_DICOMWEB_MAX_STOW_PARTS"
        printf '  qido_default_media_type: %s\n' "$(yaml_quote "$CUSTOM_DICOMWEB_MEDIA_TYPE")"
        printf '  default_transfer_syntax: %s\n' "$(yaml_quote "$CUSTOM_DICOMWEB_TRANSFER_SYNTAX")"
        printf '  auth_schemes: %s\n' "$(yaml_inline_list "$CUSTOM_DICOMWEB_AUTH_SCHEMES")"
    } >"$tmp"
    mv "$tmp" "$target"
    # The profile contains no secrets and must be readable by container uid 999.
    chmod 0644 "$target"
    PROFILE=/opt/dicomhawk/profiles/custom.yaml
    green "Wrote $target"
}

write_override() {
    local marker="# Generated by setup.sh. Do not edit; re-run the script instead."
    if [[ $PORTS == "$DEFAULT_PORTS" && $WEB_PORT == "$DEFAULT_WEB_PORT" && $OPERATOR_PORT == "$DEFAULT_OPERATOR_PORT" ]] && (( ! CUSTOM_PROFILE )); then
        # Nothing to correct, and leaving a stale file behind would publish the previous run's ports.
        rm -f "$OVERRIDE_FILE"
        return
    fi

    local tmp
    tmp=$(mktemp "$REPO_ROOT/.compose.tmp.XXXXXX")
    {
        echo "$marker"
        echo "#"
        echo "# Compose appends 'ports' lists when it merges files, so a custom port would be published"
        echo "# alongside the base 104:104 that nothing listens on. '!override' replaces the list instead."
        echo "services:"
        echo "  dimse:"
        echo "    ports: !override"
        local port
        for port in ${PORTS//,/ }; do
            printf '      - "%s:%s"\n' "$port" "$port"
        done
        echo "  web:"
        echo "    ports: !override"
        printf '      - "%s:%s"\n' "$WEB_PORT" "$WEB_PORT"
        echo "  operator:"
        echo "    ports: !override"
        printf '      - "127.0.0.1:%s:%s"\n' "$OPERATOR_PORT" "$OPERATOR_PORT"
        echo "  dicomweb:"
        if (( CUSTOM_PROFILE )) && [[ $CUSTOM_DICOMWEB_ENABLED == false ]]; then
            echo "    ports: !override []"
        else
            echo "    ports: !override"
        fi
        if (( CUSTOM_PROFILE )) && [[ $CUSTOM_DICOMWEB_ENABLED == true ]]; then
            printf '      - "%s:%s"\n' "$CUSTOM_DICOMWEB_PORT" "$CUSTOM_DICOMWEB_PORT"
        elif (( ! CUSTOM_PROFILE )); then
            for port in "${DICOMWEB_PUBLISHED[@]}"; do
                printf '      - "%s:%s"\n' "$port" "$port"
            done
        fi
    } >"$tmp"
    mv "$tmp" "$OVERRIDE_FILE"

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
    box --inputbox "$1" 12 "$DIALOG_WIDTH" "$2"
}

ask_secret() {  # <prompt>
    (( USE_DEFAULTS )) && { printf ''; return 0; }
    box --passwordbox "$1" 11 "$DIALOG_WIDTH"
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
        19 "$DIALOG_WIDTH"
}

step_profile() {
    (( USE_DEFAULTS )) && return 0
    local -a options=() names=()
    local dir name choice
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
        options+=("custom" "Configure a complete PACS identity, DICOM, web, bait, fingerprint, and DICOMweb profile" \
            "$( (( CUSTOM_PROFILE )) && echo ON || echo OFF )")
        choice=$(box --radiolist \
            "Which device should the honeypot impersonate?\n\nA profile drives the advertised identity, the accepted SOP classes, and the web surface." \
            $(( ${#names[@]} + 11 )) "$DIALOG_WIDTH" $(( ${#names[@]} + 2 )) "${options[@]}") || return $?

        case $choice in
            none) PROFILE=""; CUSTOM_PROFILE=0; return 0 ;;
            custom)
                CUSTOM_PROFILE=1
                PROFILE=/opt/dicomhawk/profiles/custom.yaml
                [[ -n $AE_TITLE ]] || AE_TITLE=ORTHANC
                return 0 ;;
            *) PROFILE=$choice; CUSTOM_PROFILE=0; return 0 ;;
        esac
    done
}

custom_profile_skipped() { (( CUSTOM_PROFILE )) || return "$SKIP"; }

step_custom_profile_name() {
    custom_profile_skipped || return $?
    local value
    value=$(ask "Custom profile name.\n\nExample: clinic-pacs" "$CUSTOM_PROFILE_NAME") || return $?
    [[ -n $value ]] && CUSTOM_PROFILE_NAME=$value
}

step_custom_implementation_uid() {
    custom_profile_skipped || return $?
    local value
    value=$(ask "DICOM Implementation Class UID.\n\nThis is a numeric DICOM UID, not a UUID.\nLegacy generic default: 1.2.826.0.1.3680043.9.3811.2.0.1" "$CUSTOM_IMPLEMENTATION_UID") || return $?
    [[ -n $value ]] && CUSTOM_IMPLEMENTATION_UID=$value
}

step_custom_implementation_version() {
    custom_profile_skipped || return $?
    local value
    value=$(ask "Implementation Version Name (maximum 16 characters).\n\nLegacy generic default: ORTHANC" "$CUSTOM_IMPLEMENTATION_VERSION") || return $?
    [[ -n $value ]] && CUSTOM_IMPLEMENTATION_VERSION=$value
}

step_custom_manufacturer() {
    custom_profile_skipped || return $?
    local value
    value=$(ask "Manufacturer shown in seeded DICOM metadata.\n\nExamples: Orthanc, GE MEDICAL SYSTEMS, SIEMENS" "$CUSTOM_MANUFACTURER") || return $?
    CUSTOM_MANUFACTURER=$value
}

step_custom_model() {
    custom_profile_skipped || return $?
    local value
    value=$(ask "Model name shown in seeded DICOM metadata.\n\nExamples: Generic PACS, PACS-RS, Clinic Archive" "$CUSTOM_MODEL") || return $?
    CUSTOM_MODEL=$value
}

custom_ask() {  # <variable> <question-with-example>
    custom_profile_skipped || return $?
    local variable=$1 question=$2 value
    value=$(ask "$question" "${!variable}") || return $?
    printf -v "$variable" '%s' "$value"
}

step_custom_operations() { custom_ask CUSTOM_OPERATIONS "Enabled DIMSE operations, comma-separated.\n\nExample/default: echo,find,get,move,store\nChoices: echo, find, get, move, store"; }
step_custom_max_associations() { custom_ask CUSTOM_MAX_ASSOCIATIONS "Maximum simultaneous DICOM associations.\n\nExample/default: 16"; }
step_custom_max_pdu() { custom_ask CUSTOM_MAX_PDU_SIZE "Maximum negotiated PDU size in bytes.\n\nExample/default: 65536"; }
step_custom_acse_timeout() { custom_ask CUSTOM_ACSE_TIMEOUT "ACSE negotiation timeout in seconds.\n\nExample/default: 10"; }
step_custom_network_timeout() { custom_ask CUSTOM_NETWORK_TIMEOUT "Idle network timeout in seconds.\n\nExample/default: 15"; }
step_custom_dimse_timeout() { custom_ask CUSTOM_DIMSE_TIMEOUT "DIMSE operation timeout in seconds.\n\nExample/default: 20"; }
step_custom_max_store() { custom_ask CUSTOM_MAX_STORE_BYTES "Maximum accepted C-STORE object size in bytes.\n\nExample/default: 67108864 (64 MiB)"; }
step_custom_require_called() { custom_ask CUSTOM_REQUIRE_CALLED_AET "Require the called AE title to match this profile?\n\nExample/default: false\nEnter exactly true or false."; }
step_custom_require_calling() { custom_ask CUSTOM_REQUIRE_CALLING_AETS "Allowed calling AE titles, comma-separated.\n\nExample: MODALITY1,WORKSTATION\nLeave empty to accept every calling AE title."; }
step_custom_web_enabled() { custom_ask CUSTOM_WEB_ENABLED "Enable the attacker-facing PACS web surface?\n\nExample/default: true\nEnter exactly true or false."; }
step_custom_web_browse() { custom_ask CUSTOM_WEB_BROWSE "Enable the post-login DICOM browser and upload page?\n\nExample/default: true\nEnter exactly true or false."; }
step_custom_web_grant() { custom_ask CUSTOM_WEB_GRANT_ACCESS "Which submitted logins may enter the decoy?\n\nExample/default: keyword\nChoices: none, bait, keyword, any"; }
step_custom_web_secure() { custom_ask CUSTOM_WEB_SECURE_COOKIES "Mark the profile's session cookie Secure?\n\nExample: false for HTTP; true when attackers use HTTPS.\nEnter exactly true or false."; }
step_custom_honey_username() { custom_ask CUSTOM_HONEY_USERNAME "Honey credential username.\n\nExample/default: test\nThis is deliberate bait, never a real account."; }
step_custom_honey_password() { custom_ask CUSTOM_HONEY_PASSWORD "Honey credential password.\n\nExample/default: test\nThis is deliberate bait, never a real password."; }
step_custom_honey_keywords() { custom_ask CUSTOM_HONEY_KEYWORDS "Login keywords admitted by grant_access=keyword, comma-separated.\n\nExample/default: admin,pacs,dicom,radiology,imaging,service"; }
step_custom_web_server() { custom_ask CUSTOM_WEB_SERVER "HTTP Server response-header identity.\n\nExamples: Apache, Microsoft-IIS/10.0, nginx"; }
step_custom_web_site() { custom_ask CUSTOM_WEB_SITE_NAME "Site name displayed by the generic PACS pages.\n\nExamples: Generic PACS, City Hospital Imaging"; }
step_custom_web_version() { custom_ask CUSTOM_WEB_VERSION "Product version displayed by the web identity.\n\nExamples: 1.0, 7.4.300"; }
step_custom_web_prefix() { custom_ask CUSTOM_WEB_ROUTE_PREFIX "URL prefix used to generate every web route.\n\nExample/default: /portal\nStart with / and do not add a trailing slash."; }
step_custom_cookie_prefix() { custom_ask CUSTOM_WEB_COOKIE_PREFIX "Prefix used to generate profile-specific cookie names.\n\nExamples: portal, clinic_pacs"; }
step_custom_web_request_limit() { custom_ask CUSTOM_WEB_MAX_REQUEST_BYTES "Maximum ordinary web request size in bytes.\n\nExample/default: 1048576 (1 MiB)"; }
step_custom_upload_limit() { custom_ask CUSTOM_UPLOAD_MAX_REQUEST_BYTES "Maximum upload request size in bytes.\n\nExample/default: 52428800 (50 MiB)"; }
step_custom_upload_files() { custom_ask CUSTOM_UPLOAD_MAX_FILES "Maximum DICOM files in one web upload (1-100).\n\nExample/default: 10"; }
step_custom_page_size() { custom_ask CUSTOM_PAGE_SIZE "Rows returned per browse/worklist page (1-500).\n\nExample/default: 100"; }
step_custom_fingerprint_enabled() { custom_ask CUSTOM_FINGERPRINT_ENABLED "Enable browser fingerprint collection for this profile?\n\nExample/default: true\nEnter exactly true or false."; }
step_custom_fingerprint_signals() { custom_ask CUSTOM_FINGERPRINT_SIGNALS "Fingerprint signal groups, comma-separated.\n\nExample/default: browser,rendering,math,screen,bot"; }
step_custom_dicomweb_enabled() { custom_ask CUSTOM_DICOMWEB_ENABLED "Enable DICOMweb for this profile?\n\nExample/default: true\nEnter exactly true or false."; }
step_custom_dicomweb_services() { custom_ask CUSTOM_DICOMWEB_SERVICES "DICOMweb services, comma-separated.\n\nExample/default: qido,wado_rs,stow\nChoices: qido, wado_rs, stow, wado_uri"; }
step_custom_dicomweb_port() { custom_ask CUSTOM_DICOMWEB_PORT "Port shared by the custom DICOMweb services.\n\nExample/default: 8042"; }
step_custom_dicomweb_path() { custom_ask CUSTOM_DICOMWEB_BASE_PATH "Base URL path shared by DICOMweb services.\n\nExample/default: /dicom-web"; }
step_custom_dicomweb_auth_services() { custom_ask CUSTOM_DICOMWEB_REQUIRE_AUTH "DICOMweb services requiring an authentication challenge.\n\nExample: qido,wado_uri\nLeave empty for none; names must also be enabled."; }
step_custom_dicomweb_qido_limit() { custom_ask CUSTOM_DICOMWEB_QIDO_MAX_RESULTS "Maximum QIDO results returned per query.\n\nExample/default: 20000"; }
step_custom_dicomweb_request_limit() { custom_ask CUSTOM_DICOMWEB_MAX_REQUEST_BYTES "Maximum complete STOW request size in bytes.\n\nExample/default: 67108864 (64 MiB)"; }
step_custom_dicomweb_parts() { custom_ask CUSTOM_DICOMWEB_MAX_STOW_PARTS "Maximum MIME parts in one STOW request.\n\nExample/default: 128"; }
step_custom_dicomweb_media() { custom_ask CUSTOM_DICOMWEB_MEDIA_TYPE "Default QIDO response media type.\n\nExample/default: application/dicom+json\nChoices: application/dicom+json or application/json"; }
step_custom_dicomweb_syntax() { custom_ask CUSTOM_DICOMWEB_TRANSFER_SYNTAX "Default WADO transfer syntax UID.\n\nExample/default: 1.2.840.10008.1.2.1 (Explicit VR Little Endian)"; }
step_custom_dicomweb_schemes() { custom_ask CUSTOM_DICOMWEB_AUTH_SCHEMES "Authentication challenge schemes, comma-separated.\n\nExample/default: Basic\nChoices: Basic, Negotiate, NTLM"; }

step_ae_title() {
    local value
    value=$(ask "AE title to advertise.\n\nExamples: ORTHANC, CLINIC_PACS, SYNAPSEDICOMSCP\nLeave empty to use the profile's own. Overriding it can contradict the device you are impersonating." "$AE_TITLE") || return $?
    AE_TITLE=$value
}

step_ports() {
    local value
    while :; do
        value=$(ask "DIMSE port(s) to listen on, comma-separated.\n\nExamples: 104 or 11112,2762\n104 is the standard DICOM port and the most convincing choice." "$PORTS") || return $?
        [[ -n $value ]] || return 0
        valid_port_list "$value" && { PORTS=$value; return 0; }
        reject_port "DIMSE port list" "$value"
    done
}

step_web_port() {
    local value
    while :; do
        value=$(ask "Port for the attacker-facing web interface.\n\nExamples: 8080 for HTTP or an unpublished backend port behind a proxy." "$WEB_PORT") || return $?
        [[ -n $value ]] || return 0
        valid_port "$value" && { WEB_PORT=$value; return 0; }
        reject_port "web port" "$value"
    done
}

step_operator_port() {
    local value
    while :; do
        value=$(ask "Port for the operator API (published on host loopback only).\n\nExample/default: 8081" "$OPERATOR_PORT") || return $?
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
            15 "$DIALOG_WIDTH" 3 \
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
        12 "$DIALOG_WIDTH" 2 \
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
    value=$(ask "X-Backendserver header value.\n\nExample: SYNWEB01\nChange it per deployment; a shared value is a fingerprint." "$BACKEND_SERVER") || return $?
    BACKEND_SERVER=$value
}

step_public_base_url() {
    local value
    value=$(ask "External HTTPS origin, if this sits behind a TLS proxy (optional).\n\nExample: https://pacs.example.org\nUse only scheme + host + optional port; do not add a path." "$PUBLIC_BASE_URL") || return $?
    PUBLIC_BASE_URL=$value
}

step_trusted_proxy() {
    local value
    value=$(ask "Exact reverse-proxy IP trusted for forwarded client identity (optional).\n\nExamples: 172.18.0.2 or 2001:db8::10\nEnter one IP address, not a subnet or hostname." "$TRUSTED_PROXY") || return $?
    TRUSTED_PROXY=$value
}

step_transport() {
    (( USE_DEFAULTS )) && return 0
    local choice
    choice=$(box --menu \
        "How will attackers reach the web surface?\n\nProfiles that model an HTTPS product mark their session cookie Secure. A browser discards such a cookie over plain HTTP, so the decoy login accepts the bait credential and then silently drops the session." \
        18 "$DIALOG_WIDTH" 2 \
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
        16 "$DIALOG_WIDTH" 2 \
        "seed" "Download a sample from TCIA and seed" \
        "skip" "Start with an empty database") || return $?

    [[ $choice == seed ]] && DO_SEED=1 || DO_SEED=0
    return 0
}

seeding_skipped() { (( DO_SEED )) || return "$SKIP"; }

step_seed_collection() {
    seeding_skipped || return $?
    local value
    value=$(ask "TCIA collection name(s), comma-separated.\n\nExamples: TCGA-LUAD or TCGA-LUAD,TCGA-BRCA,CPTAC-PDA\nNames are exact and case-sensitive. See docs/seeding-values.md for the command that lists every current TCIA collection." "$SEED_COLLECTION") || return $?
    [[ -n $value ]] && SEED_COLLECTION=$value
    return 0
}

step_seed_modality() {
    seeding_skipped || return $?
    local value
    value=$(ask "DICOM modality/modalities, comma-separated.\n\nExamples: CT or CT,MR,PT\nThe modality must exist in the chosen TCIA collection. See docs/seeding-values.md for lookup commands." "$SEED_MODALITY") || return $?
    [[ -n $value ]] && SEED_MODALITY=$value
    return 0
}

step_seed_max_series() {
    seeding_skipped || return $?
    local value
    value=$(ask "Maximum series to download.\n\nExamples: 1 for a quick test, 3 by default, or 10 for more variety.\nThis is an upper bound, not a series number." "$SEED_MAX_SERIES") || return $?
    [[ -n $value ]] && SEED_MAX_SERIES=$value
    return 0
}

step_seed_max_images() {
    seeding_skipped || return $?
    local value
    value=$(ask "Images per series.\n\nExamples: 5 for a quick test, 30 by default, or 150 for a more realistic CT series.\nLarger values use more download time and storage." "$SEED_MAX_IMAGES") || return $?
    [[ -n $value ]] && SEED_MAX_IMAGES=$value
    return 0
}

step_seed_locale() {
    seeding_skipped || return $?
    local value
    value=$(ask "Faker locale for generated patient and physician names.\n\nExamples: en_US, en_IN, de_DE, fr_FR, ja_JP\nUse underscore, not a hyphen. See docs/seeding-values.md for every installed locale." "$SEED_LOCALE") || return $?
    [[ -n $value ]] && SEED_LOCALE=$value
    return 0
}

step_osm_choice() {
    seeding_skipped || return $?
    (( USE_DEFAULTS )) && return 0
    local choice
    choice=$(box --menu \
        "Where should institution names come from?" 13 "$DIALOG_WIDTH" 2 \
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
    value=$(ask "City to query in OpenStreetMap.\n\nExamples: Bengaluru, Boston, Berlin, Tokyo\nPair it with the country code so a same-named city resolves correctly." "$SEED_OSM_CITY") || return $?
    SEED_OSM_CITY=$value
}

step_osm_country() {
    osm_skipped || return $?
    local value
    value=$(ask "ISO 3166-1 alpha-2 country code.\n\nExamples: IN (India), US (United States), DE (Germany), JP (Japan)\nUse two uppercase letters. See docs/seeding-values.md for the command that lists every country code." "$SEED_OSM_COUNTRY") || return $?
    SEED_OSM_COUNTRY=$value
}

step_honey_url() {
    seeding_skipped || return $?
    local value
    value=$(ask "Honeytoken URL baked into one seeded instance.\n\nExample/default from the previous DICOMHawk customizer:\nhttps://example.com/honey\n\nReplace it with a URL you monitor, or leave it empty to disable URL bait." "$SEED_HONEY_URL") || return $?
    SEED_HONEY_URL=$value
}

step_canary_pdf() {
    seeding_skipped || return $?
    local value
    value=$(ask "Absolute path to a PDF canary file on this host (optional).\n\nExample: /home/alice/canary.pdf\nThe installer mounts it read-only into the one-shot seed container." "$SEED_CANARY_PDF") || return $?
    SEED_CANARY_PDF=$value
}

step_review() {
    (( USE_DEFAULTS )) && return 0
    PREVIEW_FILE=$(mktemp)
    {
        echo "Profile:            ${PROFILE:-<none, plain DICOM>}"
        if (( CUSTOM_PROFILE )); then
            echo "Custom identity:    $CUSTOM_PROFILE_NAME / $CUSTOM_IMPLEMENTATION_UID / $CUSTOM_IMPLEMENTATION_VERSION"
            echo "Custom DIMSE:       $CUSTOM_OPERATIONS; max associations $CUSTOM_MAX_ASSOCIATIONS; max store $CUSTOM_MAX_STORE_BYTES bytes"
            echo "Custom web:         enabled=$CUSTOM_WEB_ENABLED browse=$CUSTOM_WEB_BROWSE access=$CUSTOM_WEB_GRANT_ACCESS route=$CUSTOM_WEB_ROUTE_PREFIX"
            echo "Custom fingerprint: enabled=$CUSTOM_FINGERPRINT_ENABLED signals=$CUSTOM_FINGERPRINT_SIGNALS"
            echo "Custom DICOMweb:    enabled=$CUSTOM_DICOMWEB_ENABLED services=$CUSTOM_DICOMWEB_SERVICES port=$CUSTOM_DICOMWEB_PORT path=$CUSTOM_DICOMWEB_BASE_PATH"
        fi
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
            echo "Honeytoken URL:     ${SEED_HONEY_URL:-<disabled>}"
            echo "Canary PDF:         ${SEED_CANARY_PDF:-<disabled>}"
        else
            echo "Seed:               skipped"
        fi
    } >"$PREVIEW_FILE"

    whiptail --title "$TITLE" --backtitle "$BACKTITLE" \
        --yes-button "Write it" --no-button "Back" \
        --yesno "$(cat "$PREVIEW_FILE")" "$(( CUSTOM_PROFILE ? 29 : 24 ))" "$DIALOG_WIDTH"
}

ask_everything() {
    run_steps \
        step_welcome step_profile step_custom_profile_name step_ae_title \
        step_custom_implementation_uid step_custom_implementation_version \
        step_custom_manufacturer step_custom_model \
        step_custom_operations step_custom_max_associations step_custom_max_pdu \
        step_custom_acse_timeout step_custom_network_timeout step_custom_dimse_timeout \
        step_custom_max_store step_custom_require_called step_custom_require_calling \
        step_custom_web_enabled step_custom_web_browse step_custom_web_grant \
        step_custom_web_secure step_custom_honey_username step_custom_honey_password \
        step_custom_honey_keywords step_custom_web_server step_custom_web_site \
        step_custom_web_version step_custom_web_prefix step_custom_cookie_prefix \
        step_custom_web_request_limit step_custom_upload_limit step_custom_upload_files \
        step_custom_page_size step_custom_fingerprint_enabled step_custom_fingerprint_signals \
        step_custom_dicomweb_enabled step_custom_dicomweb_services step_custom_dicomweb_port \
        step_custom_dicomweb_path step_custom_dicomweb_auth_services \
        step_custom_dicomweb_qido_limit step_custom_dicomweb_request_limit \
        step_custom_dicomweb_parts step_custom_dicomweb_media step_custom_dicomweb_syntax \
        step_custom_dicomweb_schemes \
        step_ports step_web_port step_operator_port \
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
        "A configuration already exists at .env.\n\nWhat would you like to do?" 15 "$DIALOG_WIDTH" 3 \
        "reconfigure" "Answer the questions again and overwrite it" \
        "keep" "Keep it and just build and start" \
        "abort" "Change nothing and exit") || cancelled

    case $choice in
        keep) KEEP_EXISTING=1; DO_SEED=0 ;;
        abort) cancelled ;;
    esac
}

# ---- run ----

compose_failed() {  # <what>
    red "$1 failed."
    "${DOCKER[@]}" compose logs --tail=50 2>/dev/null || true
    exit 1
}

wait_for_health() {
    local waited=0 status
    info "Waiting for the DIMSE container to report healthy…"
    while (( waited < HEALTH_TIMEOUT )); do
        status=$("${DOCKER[@]}" compose ps --format '{{.Health}}' dimse 2>/dev/null | head -n 1)
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
    local -a mount_args=()
    if [[ -n $SEED_CANARY_PDF ]]; then
        mount_args=(-v "$SEED_CANARY_PDF:/run/dicomhawk/canary.pdf:ro")
        args+=(--canary-pdf /run/dicomhawk/canary.pdf)
    fi

    info "Seeding the database…"
    echo "    source     $SEED_COLLECTION ($SEED_MODALITY), up to $SEED_MAX_SERIES series x $SEED_MAX_IMAGES images"
    [[ -n $SEED_OSM_CITY ]] && echo "    hospitals  OpenStreetMap: $SEED_OSM_CITY"
    echo "    This downloads from TCIA and can take several minutes. Progress is printed per series."
    # Non-fatal: an unreachable TCIA still leaves a working honeypot with the offline fallback.
    if ! "${DOCKER[@]}" compose run --rm --no-deps "${mount_args[@]}" dimse dicomhawk seed "${args[@]}"; then
        red "Seeding failed. The honeypot is running; re-run 'docker compose run --rm dimse dicomhawk seed' once the source is reachable."
    fi
}

# Asked of the container so routes come from the profile loader, not a second copy kept here.
profile_endpoints() {
    "${DOCKER[@]}" compose exec -T dimse python3 -c '
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
    local -a reseed=(docker compose run --rm --no-deps)
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
    echo "  Event log        $DATA_DIR/logs/dicomhawk.log"
    echo "  Service logs     docker compose logs -f"
    echo "  Stop             docker compose down"
    if [[ -n $SEED_CANARY_PDF ]]; then
        reseed+=(-v "$SEED_CANARY_PDF:/run/dicomhawk/canary.pdf:ro")
    fi
    reseed+=(dimse dicomhawk seed
        --collection "$SEED_COLLECTION"
        --modality "$SEED_MODALITY"
        --max-series "$SEED_MAX_SERIES"
        --max-images "$SEED_MAX_IMAGES"
        --locale "$SEED_LOCALE")
    [[ -n $SEED_OSM_CITY ]] && reseed+=(--osm-city "$SEED_OSM_CITY")
    [[ -n $SEED_OSM_COUNTRY ]] && reseed+=(--osm-country "$SEED_OSM_COUNTRY")
    [[ -n $SEED_HONEY_URL ]] && reseed+=(--honey-url "$SEED_HONEY_URL")
    [[ -n $SEED_CANARY_PDF ]] && reseed+=(--canary-pdf /run/dicomhawk/canary.pdf)
    printf '  Re-seed example  '
    printf '%q ' "${reseed[@]}"
    echo
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
        validate_answers
        if ports_collide; then
            die "The same host port was chosen more than once; DIMSE, web, and operator ports must differ."
        fi
        warn_busy_ports
        prepare_data_layout
        write_custom_profile
        write_env
        write_override
    fi

    if (( ! DO_START )); then
        green "Configuration written. Start it with: docker compose up -d"
        return 0
    fi

    prepare_data_layout

    info "Building the image…"
    "${DOCKER[@]}" compose build || compose_failed "Build"

    info "Starting the stack…"
    "${DOCKER[@]}" compose up -d || compose_failed "Startup"

    wait_for_health
    (( DO_SEED )) && run_seed
    summary
}

main "$@"

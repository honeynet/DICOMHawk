#!/bin/sh
# DICOMHawk Log Cleaner & Rotator

RED="\033[0;31m"
GREEN="\033[0;32m"
WHITE="\033[0;0m"

PIGZ=$(which pigz)
if [ -z "$PIGZ" ]; then
    PIGZ=$(which gzip)
fi

setup_logrotate_conf() {
    local LOGROTATE_CONF="/opt/dicomhawk/etc/logrotate/logrotate.conf"
    local LOGROTATE_TEMP="/opt/dicomhawk/etc/logrotate/logrotate.template"
    envsubst < $LOGROTATE_TEMP > $LOGROTATE_CONF
    chmod 644 $LOGROTATE_CONF
}

setup_directories() {
    mkdir -p /data/dicomhawk/logs/pynetdicom
    mkdir -p /data/dicomhawk/logs/simplified
    mkdir -p /data/dicomhawk/logs/exceptions
    mkdir -p /data/dicomhawk/logs/api_logs
    mkdir -p /data/dicomhawk/logs/reputation
    mkdir -p /data/dicomhawk/logs/scanned_ips
    chmod 770 /data/dicomhawk/logs -R

    mkdir -p /data/dicomhawk/etc/logrotate
    chmod 755 /data/dicomhawk/etc/logrotate
}

rotate_logs() {
    local STATUS="/data/dicomhawk/etc/logrotate/status"
    local CONF="/opt/dicomhawk/etc/logrotate/logrotate.conf"

    setup_logrotate_conf

    logrotate -f -s $STATUS $CONF

    # Compress any uncompressed rotated logs
    find /data/dicomhawk/logs -type f -name "*.log.*" ! -name "*.gz" -exec $PIGZ -f {} \;
}


echo -e "${GREEN}Starting DICOMHawk log management...${WHITE}"

setup_directories

rotate_logs

echo -e "${GREEN}Log management completed!${WHITE}" 
#!/bin/sh

# Setup cron job with environment variable
echo "0 0 * * * /opt/dicomhawk/bin/clean.sh ${PERSISTENCE_CYCLES:-30}" > /etc/crontabs/root

# Run clean script once at startup
/opt/dicomhawk/bin/clean.sh ${PERSISTENCE_CYCLES:-30}

# Start cron daemon to run logrotate periodically
crond -f -l 2 
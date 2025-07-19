FROM alpine:latest

# Install required packages
RUN apk add --no-cache \
    logrotate \
    pigz \
    gettext

# Copy dist directory
COPY dist/ /opt/dicomhawk/

# Set permissions
RUN chmod 755 /opt/dicomhawk/bin/clean.sh \
    && chmod 755 /opt/dicomhawk/entrypoint.sh \
    && chmod 644 /opt/dicomhawk/etc/logrotate/logrotate.template

ENTRYPOINT ["/opt/dicomhawk/entrypoint.sh"] 
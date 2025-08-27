FROM alpine:latest

# Use UID 2000 to match T-Pot's security model
ARG APP_UID=2000
ARG APP_GID=2000

# Install required packages
RUN apk add --no-cache \
    logrotate \
    pigz \
    gettext

# Copy dist directory
COPY dist/ /opt/dicomhawk/

RUN addgroup -g ${APP_GID} dicom \
    && adduser -D -u ${APP_UID} -G dicom -s /bin/sh dicom \
    && chown -R ${APP_UID}:${APP_GID} /opt/dicomhawk

# Set permissions
RUN chmod 755 /opt/dicomhawk/bin/clean.sh \
    && chmod 755 /opt/dicomhawk/entrypoint.sh \
    && chmod 644 /opt/dicomhawk/etc/logrotate/logrotate.template

USER ${APP_UID}:${APP_GID}

ENTRYPOINT ["/opt/dicomhawk/entrypoint.sh"] 
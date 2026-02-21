FROM python:3.12-slim

WORKDIR /app

ARG APP_UID=2000
ARG APP_GID=2000

RUN groupadd -g ${APP_GID} dicom && \
    useradd -m -u ${APP_UID} -g ${APP_GID} -s /usr/sbin/nologin dicom

# Create all runtime directories the app expects
RUN mkdir -p \
        /opt/dicomhawk/storage/dicom_storage \
        /opt/dicomhawk/storage/c_store_files \
        /opt/dicomhawk/tcia/data \
        /opt/dicomhawk/tcia/stagger \
        /var/log/dicomhawk/dicom_raw_logs \
        /var/log/dicomhawk/simplified \
        /var/log/dicomhawk/exceptions && \
    chown -R ${APP_UID}:${APP_GID} /opt/dicomhawk /var/log/dicomhawk /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

USER ${APP_UID}:${APP_GID}

# Default: DICOM server. Override via `command:` in docker-compose.yml for logserver.
CMD ["dicomhawk", "serve"]

FROM python:3.9-slim

ARG APP_UID=2000
ARG APP_GID=2000

WORKDIR /customizer

# Copy the customizer script
COPY customizer.py /customizer/
COPY entrypoint.sh /customizer/

# Make scripts executable
RUN chmod +x /customizer/customizer.py
RUN chmod +x /customizer/entrypoint.sh

# Install required packages
RUN pip install --no-cache-dir colorama

RUN groupadd -g ${APP_GID} dicom \
    && useradd -m -u ${APP_UID} -g ${APP_GID} -s /usr/sbin/nologin dicom \
    && chown -R ${APP_UID}:${APP_GID} /customizer

USER ${APP_UID}:${APP_GID}

ENTRYPOINT ["/customizer/entrypoint.sh"] 
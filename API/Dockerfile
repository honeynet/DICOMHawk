FROM node

ARG APP_UID=2000
ARG APP_GID=2000

COPY package*.json /api/
COPY . /api/

WORKDIR /api/.

RUN groupadd -g ${APP_GID} dicom \
    && useradd -m -u ${APP_UID} -g ${APP_GID} -s /usr/sbin/nologin dicom \
    && mkdir -p /api /var/log/dicomhawk \
    && chown -R ${APP_UID}:${APP_GID} /api /var/log/dicomhawk

RUN npm install && npm rebuild sqlite3 && npm install redis \
    && chown -R ${APP_UID}:${APP_GID} /api

EXPOSE 3702

USER ${APP_UID}:${APP_GID}

CMD ["node", "app.js"]
FROM python:3.9-slim
USER root

RUN mkdir -p /var/log/dicomhawk/dicom_raw_logs \
             /var/log/dicomhawk/simplified \
             /var/log/dicomhawk/exceptions \
             /var/log/dicomhawk/api_logs \
             /var/log/dicomhawk/reputation \
             /var/log/dicomhawk/scanned_ips \
             /opt/dicomhawk/storage/dicom_storage \
             /opt/dicomhawk/storage/c_store_files \
             /opt/dicomhawk/tcia/data \
             /opt/dicomhawk/tcia/stagger

RUN touch /var/log/dicomhawk/dicom_raw_logs/dicom_raw.log \
          /var/log/dicomhawk/simplified/simplified_logger.log \
          /var/log/dicomhawk/exceptions/exceptions.log \
          /var/log/dicomhawk/api_logs/api_logs.log \
          /var/log/dicomhawk/reputation/reputation.log \
          /var/log/dicomhawk/scanned_ips/scanned_ips.log

COPY . dicom_server/
WORKDIR /dicom_server/.

RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && apt-get update \
    && apt-get -qq install -y iptables ipset \

COPY ./pydicom_and_pynetdicom_libs/service_class.py   /usr/local/lib/python3.9/site-packages/pynetdicom/service_class.py
COPY ./pydicom_and_pynetdicom_libs/db.py /usr/local/lib/python3.9/site-packages/pynetdicom/apps/qrscp/db.py
COPY ./pydicom_and_pynetdicom_libs/valuerep.py /usr/local/lib/python3.9/site-packages/pydicom/valuerep.py

ARG APP_UID=2000
ARG APP_GID=2000
RUN groupadd -g ${APP_GID} dicom \
    && useradd -m -u ${APP_UID} -g ${APP_GID} -s /usr/sbin/nologin dicom \
    && chown -R ${APP_UID}:${APP_GID} \
           /var/log/dicomhawk \
           /opt/dicomhawk \

EXPOSE 11112
USER ${APP_UID}:${APP_GID}
CMD ["python","-u","main.py"]

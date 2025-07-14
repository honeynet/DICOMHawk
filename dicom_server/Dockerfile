FROM python:3.9-slim
USER root

RUN mkdir -p /var/log/dicomhawk/pynetdicom \
             /var/log/dicomhawk/simplified \
             /var/log/dicomhawk/exceptions \
             /opt/dicomhawk/db \
             /opt/dicomhawk/storage/dicom_storage \
             /opt/dicomhawk/storage/c_store_files \
             /opt/dicomhawk/tcia/data \
             /opt/dicomhawk/tcia/stagger


COPY ./storage/db.db /opt/dicomhawk/db/

RUN touch /var/log/dicomhawk/pynetdicom/pynetdicom.log \
          /var/log/dicomhawk/simplified/simplified_logger.log \
          /var/log/dicomhawk/exceptions/exceptions.log

COPY . dicom_server/

WORKDIR /dicom_server/.

RUN pip install --upgrade pip  && pip install -r requirements.txt && apt-get update &&  apt-get -qq install iptables ipset


COPY ./pydicom_and_pynetdicom_libs/service_class.py   /usr/local/lib/python3.9/site-packages/pynetdicom/service_class.py
COPY ./pydicom_and_pynetdicom_libs/db.py /usr/local/lib/python3.9/site-packages/pynetdicom/apps/qrscp/db.py
COPY ./pydicom_and_pynetdicom_libs/valuerep.py /usr/local/lib/python3.9/site-packages/pydicom/valuerep.py

EXPOSE 11112
CMD ["python","-u" ,"main.py"]

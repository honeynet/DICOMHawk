FROM python:3.9-slim
USER root

COPY ./storage/db.db app/

RUN mkdir -p app/logs/pynetdicom app/logs/simplified app/logs/exceptions && \
    touch app/logs/pynetdicom/pynetdicom.log app/logs/simplified/simplified_logger.log app/logs/exceptions/exceptions.log

COPY . dicom_server/

WORKDIR /dicom_server/.

RUN pip install --upgrade pip  && pip install -r requirements.txt && apt-get update &&  apt-get -qq install iptables ipset


COPY ./pydicom_and_pynetdicom_libs/service_class.py   /usr/local/lib/python3.9/site-packages/pynetdicom/service_class.py
COPY ./pydicom_and_pynetdicom_libs/db.py /usr/local/lib/python3.9/site-packages/pynetdicom/apps/qrscp/db.py
COPY ./pydicom_and_pynetdicom_libs/valuerep.py /usr/local/lib/python3.9/site-packages/pydicom/valuerep.py

EXPOSE 11112
CMD ["python","-u" ,"main.py"]

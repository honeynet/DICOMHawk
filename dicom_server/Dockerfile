FROM python:3.9-slim
 
# Set the working directory in the container
 
 
# Copy the current directory contents into the container at /app
COPY . .
#Patched file
 
#COPY simplified_logs ./app/
#COPY logs ./app/
 
WORKDIR /.
 
 
# Install any needed packages specified in requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt
 
# COPY ./service_class.py   ./usr/local/lib/python3.9/site-packages/pynetdicom/service_class.py
# COPY ./db.py ./usr/local/lib/python3.9/site-packages/pynetdicom/apps/qrscp/db.py
 
# Expose the DICOM
EXPOSE 11112
 
 
# Run the app
CMD ["python","-u" ,"dicomhawk.py"]
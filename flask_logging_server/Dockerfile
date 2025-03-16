FROM python:3.8-slim

# Set the working directory in the container to /app
WORKDIR /log_server

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /app
COPY . /log_server

# Install any needed packages specified in requirements.txt
RUN pip install flask uwsgi

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Define environment variable
ENV NAME World

# Run uWSGI with the configuration
CMD ["uwsgi", "--http", "0.0.0.0:5000", "--module", "logserver:app", "--processes", "1", "--threads", "8"]

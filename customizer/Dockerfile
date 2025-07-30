FROM python:3.9-slim

WORKDIR /customizer

# Copy the customizer script
COPY customizer.py /customizer/
COPY entrypoint.sh /customizer/

# Make scripts executable
RUN chmod +x /customizer/customizer.py
RUN chmod +x /customizer/entrypoint.sh

# Install required packages
RUN pip install --no-cache-dir colorama

ENTRYPOINT ["/customizer/entrypoint.sh"] 
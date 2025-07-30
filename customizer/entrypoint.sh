#!/bin/bash

# DICOMHawk Customizer Entrypoint
# This script runs the customizer and ensures it only runs once

set -e

CONFIG_FLAG="/customizer/.configured"

echo "DICOMHawk Customizer Starting..."

# Check if already configured
if [ -f "$CONFIG_FLAG" ]; then
    echo "Configuration already completed. Skipping customizer."
    exit 0
fi

# Check if .env file exists
if [ -f "/workspace/.env" ]; then
    echo "Configuration file .env already exists."
    echo "If you want to reconfigure, remove the .env file and restart."
    exit 0
fi

# Run the customizer
echo "Starting configuration process..."
python3 /customizer/customizer.py

# Check if configuration was successful
if [ -f "/workspace/.env" ]; then
    echo "Configuration completed successfully!"
    touch "$CONFIG_FLAG"
    exit 0
else
    echo "Configuration failed. Please check the output above."
    exit 1
fi 
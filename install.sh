#!/usr/bin/env bash

# DICOMHawk Installation Script
# This script helps users install and configure DICOMHawk

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Docker is installed
check_docker() {

    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose is not installed. Please install Docker Compose first."
        exit 1
    fi
    
    print_success "Docker and Docker Compose are installed"
}

# Check if running as root
check_root() {
    if [[ $EUID -eq 0 ]]; then
        print_warning "Running as root. This is not recommended for security reasons."
        read -p "Do you want to continue? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}


create_directories() {
    print_status "Creating necessary directories..."
    
    mkdir -p data/dicomhawk/logs
    mkdir -p dicom_server/storage/dicom_storage
    mkdir -p dicom_server/storage/c_store_files
    mkdir -p dicom_server/storage/tcia_data
    mkdir -p dicom_server/storage/stagger
    
    print_success "Directories created"
}

# Check if .env file exists
check_env_file() {
    if [ -f ".env" ]; then
        print_warning "Configuration file .env already exists."
        read -p "Do you want to reconfigure? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -f .env
            print_status "Removed existing .env file"
        else
            print_status "Using existing configuration"
            return 0
        fi
    fi
    
    return 1
}

# Run the customizer
run_customizer() {
    print_status "Starting configuration process..."
    
    # Check if Python is available
    if ! command -v python3 &> /dev/null; then
        print_error "Python3 is not installed. Please install Python3 first."
        exit 1
    fi
    
    # Run the customizer
    python3 customizer/customizer.py
    
    if [ -f ".env" ]; then
        print_success "Configuration completed successfully!"
    else
        print_error "Configuration failed. Please check the output above."
        exit 1
    fi
}

# Build and start containers
start_containers() {
    print_status "Building and starting containers..."
    
    # Build the customizer first
    docker-compose build customizer
    
    # Start the main stack
    docker-compose --profile main up -d
    
    print_success "Containers started successfully!"
}

# final instructions
show_instructions() {
    echo print_success "DICOMHawk installation completed!"

    echo "
    Access points:
        - Web Interface: http://localhost:5000
        - API: http://localhost:3702
        - DICOM Server: localhost:11112

    Useful commands:
        - View logs: docker-compose logs -f
        - Stop services: docker-compose down
        - Restart services: docker-compose restart
    "

    echo print_warning "Important:
        - Update TCIA credentials if they expire
        - Monitor logs for any issues
    "

    exit 0
}

main() {
    echo '
==========================================
            DICOMHawk Installation
==========================================
    '

    check_root
    check_docker
    create_directories
    
    if check_env_file; then
        print_status "Using existing configuration"
    else
        run_customizer
    fi
    
    start_containers
    show_instructions
}

# Handle script interruption
trap 'print_error "Installation interrupted"; exit 1' INT TERM

# Run main function
main "$@" 
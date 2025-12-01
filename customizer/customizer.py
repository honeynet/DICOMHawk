#!/usr/bin/env python3
"""
DICOMHawk Configuration Customizer
This script prompts users for necessary configurations and generates a .env file
for the DICOMHawk honeypot system.
"""

import os
import sys
import secrets
import string
from pathlib import Path

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_header():
    """Print the DICOMHawk header"""
    print(f"{Colors.HEADER}{Colors.BOLD}")
    print("=" * 60)
    print("           DICOMHawk Configuration Customizer")
    print("=" * 60)
    print(f"{Colors.ENDC}")
    print("This script will help you configure DICOMHawk for your environment.")
    print("Press Enter to use default values (shown in brackets).")
    print()

def generate_secret(length=32):
    """Generate a random secret string"""
    # Use only alphanumeric characters to avoid Docker Compose variable interpretation issues
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def get_input(prompt, default="", required=False, secret=False):
    """Get user input with validation"""
    while True:
        if secret:
            import getpass
            value = getpass.getpass(f"{prompt} [{default}]: ")
        else:
            value = input(f"{prompt} [{default}]: ").strip()
        
        if not value:
            if required and not default:
                print(f"{Colors.FAIL}This field is required. Please enter a value.{Colors.ENDC}")
                continue
            value = default
        
        if required and not value:
            print(f"{Colors.FAIL}This field is required. Please enter a value.{Colors.ENDC}")
            continue
            
        return value

def check_first_run():
    """Check if this is the first run"""
    env_file = Path(".env")
    if env_file.exists():
        print(f"{Colors.WARNING}Configuration file .env already exists.{Colors.ENDC}")
        response = input("Do you want to overwrite it? (y/n): ").lower().strip()
        if response not in ['y', 'yes']:
            print("Configuration cancelled.")
            sys.exit(0)
    return True

def main():
    """Main configuration function"""
    print_header()
    
    if not check_first_run():
        return
    
    print(f"{Colors.OKBLUE}Step 1: Security Configuration{Colors.ENDC}")
    print("-" * 40)
    
    # Generate default secrets
    access_token_secret = generate_secret(32)
    refresh_token_secret = generate_secret(32)
    admin_secret = generate_secret(32)
    admin_refresh_token_secret = generate_secret(32)
    session_secret = generate_secret(32)
    
    # Security tokens
    ACCESS_TOKEN_SECRET = get_input(
        "Access Token Secret (for JWT authentication)",
        access_token_secret,
        required=True,
        secret=True
    )
    
    REFRESH_TOKEN_SECRET = get_input(
        "Refresh Token Secret (for JWT refresh)",
        refresh_token_secret,
        required=True,
        secret=True
    )
    
    ADMIN_SECRET = get_input(
        "Admin Secret (for admin authentication)",
        admin_secret,
        required=True,
        secret=True
    )
    
    ADMIN_REFRESH_TOKEN_SECRET = get_input(
        "Admin Refresh Token Secret",
        admin_refresh_token_secret,
        required=True,
        secret=True
    )
    
    SESSION_SECRET = get_input(
        "Session Secret (for session management)",
        session_secret,
        required=True,
        secret=True
    )
    
    print(f"\n{Colors.OKBLUE}Step 2: API Configuration{Colors.ENDC}")
    print("-" * 40)
    
    API_PORT = get_input("API Port", "3702")
    
    print(f"\n{Colors.OKBLUE}Step 3: TCIA Configuration{Colors.ENDC}")
    print("-" * 40)
    print("TCIA credentials are optional. Leave blank to use sample DICOM files (fallback is automatic).")
    print("Get free credentials: https://www.cancerimagingarchive.net/")
    
    TCIA_USER_NAME = get_input("TCIA Username (optional)")
    TCIA_PASSWORD = get_input("TCIA Password (optional)", secret=True)
    TCIA_PERIOD_UNIT = get_input("TCIA Period Unit (minutes/hours/days)", "minutes")
    TCIA_PERIOD = get_input("TCIA Period (frequency of downloads)", "1")
    # Always enable fallback mode; it will automatically be used when credentials are missing or TCIA is disabled
    TCIA_FALLBACK_MODE = "true"
    
    print(f"\n{Colors.OKBLUE}Step 4: Threat Intelligence APIs (Optional){Colors.ENDC}")
    print("-" * 40)
    print("These APIs are used for IP reputation checking. Leave empty if you don't have keys.")
    
    ABUSE_IP_API_KEY = get_input("AbuseIPDB API Key", "", secret=True)
    IP_QUALITY_SCORE_API_KEY = get_input("IPQualityScore API Key", "", secret=True)
    VIRUS_TOTAL_API_KEY = get_input("VirusTotal API Key", "", secret=True)
    
    print(f"\n{Colors.OKBLUE}Step 5: DICOM Configuration{Colors.ENDC}")
    print("-" * 40)
    
    DICOM_PORTS = get_input("DICOM Ports (comma-separated)", "11112")
    DICOM_IMPLEMENTATION_NAME = get_input("DICOM Implementation Name", "ORTHANC")
    DICOM_IMPLEMENTATION_UID = get_input("DICOM Implementation UID", "1.2.826.0.1.3680043.9.3811.2.0.1")
    
    print(f"\n{Colors.OKBLUE}Step 6: Regional Configuration{Colors.ENDC}")
    print("-" * 40)
    
    FAKER_LOCALE = get_input("Faker Locale (for generating patient names)", "en_US")
    OSM_ENABLED = get_input("Enable OpenStreetMap integration (true/false)", "true")
    OSM_COUNTRY = get_input("OSM Country Code (ISO 3166-1 alpha-2)", "DK")
    OSM_CITY = get_input("OSM City (optional)", "")
    
    print(f"\n{Colors.OKBLUE}Step 7: Honeypot Configuration{Colors.ENDC}")
    print("-" * 40)
    
    HONEY_URL = get_input("Honey URL (for honeytoken injection)", "https://example.com/honey")
    
    # Generate .env file
    env_content = f"""# DICOMHawk Configuration File
# Generated by customizer.py

# Security Tokens
ACCESS_TOKEN_SECRET={ACCESS_TOKEN_SECRET}
REFRESH_TOKEN_SECRET={REFRESH_TOKEN_SECRET}
ADMIN_SECRET={ADMIN_SECRET}
ADMIN_REFRESH_TOKEN_SECRET={ADMIN_REFRESH_TOKEN_SECRET}
SESSION_SECRET={SESSION_SECRET}

# API Configuration
API_PORT={API_PORT}

# TCIA Configuration
TCIA_USER_NAME={TCIA_USER_NAME}
TCIA_PASSWORD={TCIA_PASSWORD}
TCIA_PERIOD_UNIT={TCIA_PERIOD_UNIT}
TCIA_PERIOD={TCIA_PERIOD}
TCIA_FALLBACK_MODE={TCIA_FALLBACK_MODE}

# Threat Intelligence APIs
ABUSE_IP_API_KEY={ABUSE_IP_API_KEY}
IP_QUALITY_SCORE_API_KEY={IP_QUALITY_SCORE_API_KEY}
VIRUS_TOTAL_API_KEY={VIRUS_TOTAL_API_KEY}

# DICOM Configuration
DICOM_PORTS={DICOM_PORTS}
DICOM_IMPLEMENTATION_NAME={DICOM_IMPLEMENTATION_NAME}
DICOM_IMPLEMENTATION_UID={DICOM_IMPLEMENTATION_UID}

# Regional Configuration
FAKER_LOCALE={FAKER_LOCALE}
OSM_ENABLED={OSM_ENABLED}
OSM_COUNTRY={OSM_COUNTRY}
OSM_CITY={OSM_CITY}

# Honeypot Configuration
HONEY_URL={HONEY_URL}
"""
    
    # Write .env file
    with open(".env", "w") as f:
        f.write(env_content)
    
    print(f"\n{Colors.OKGREEN}Configuration completed successfully!{Colors.ENDC}")
    print(f"{Colors.BOLD}Generated .env file with your configuration.{Colors.ENDC}")
    print()
    print("Next steps:")
    print("1. Review the generated .env file")
    print("2. Run: docker-compose --profile main up -d")
    print("3. Access the web interface at: http://localhost:5000")
    print("4. Access the API at: http://localhost:3702")
    print()
    print(f"{Colors.WARNING}Important:{Colors.ENDC}")
    print("- TCIA credentials are optional - the system will use sample files if not provided")
    print("- Update your TCIA credentials if they expire")
    print("- The fallback system ensures the honeypot always has realistic DICOM data")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.FAIL}Configuration cancelled by user.{Colors.ENDC}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.FAIL}Error: {e}{Colors.ENDC}")
        sys.exit(1) 
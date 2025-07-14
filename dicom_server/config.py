"""This module defines configuration constants and paths for the server service.
Many values can be overridden via environment variables

TCIA Serviceconstants:
---------------------
* TCIA_USER_NAME
* TCIA_ACTIVATED
* TCIA_PASSWORD
* TCIA_PERIOD_UNIT
* TCIA_PERIOD
* TCIA_FILES_DIRECTORY
* MODALITIES
* MINIMUM_TCIA_FILES_IN_SERIE
* MAXIMUM_TCIA_FILES_IN_SERIE
* TCIA_STUDIES_PER_MODALITY
* FAKER_LOCALE

OpenStreetMap Integration:
---------------------
* OSM_ENABLED
* OSM_COUNTRY
* OSM_CITY
* OSM_CACHE_DURATION
* OSM_MAX_INSTITUTIONS
* OSM_TIMEOUT
* OSM_FALLBACK_INSTITUTIONS

Logging Server:
---------------------
* FLASK_ACTIVATED (Important to avoid logging on test environment)
* MAIN_LOG_DIRECTORY
* SIMPLIFIED_LOG_DIRECTORY
* EXCEPTIONS_LOG_DIRECTORY

Integrity Check:
---------------------
* INTEGRITY_CHECK
* HASH_STORAGE_PATH

Threat Intelligence:
---------------------
* ABUSE_IP_API_KEY
* IP_QUALITY_SCORE_API_KEY
* VIRUS_TOTAL_API_KEY

Blackhole:
---------------------
* BLOCK_SCANNERS
* BLACKHOLE_FILE_PATH

DICOM server:
---------------------
* PROD (environment will be production if this constant is true and development if it is false)
* DICOM_STORAGE_DIR
* C_STORE_STORAGE
* DICOM_PORTS
* DICOM_SERVER_HOST
* REDIS_HOST
* DICOM_DATABASE
* CANARY_PDF_PATH

"""

import os, json

TRUE_LIST = ["true", "1", "t", "yes"]
"""Envirnoment"""
PROD = os.getenv("PROD", "False").lower() in TRUE_LIST

DOCKER = os.getenv("DOCKER", "False").lower() in TRUE_LIST
"""Flask server status"""
FLASK_ACTIVATED = os.getenv("FLASK_ACTIVATED", "True").lower() in TRUE_LIST

"""Null routing the incomming requests if belong a known mass scanner """
BLOCK_SCANNERS = os.getenv("BLOCK_SCANNERS", "False").lower() in TRUE_LIST

"""Blackhole list file"""
BLACKHOLE_FILE_PATH = os.getenv("BLACKHOLE_FILE_PATH", "/opt/dicomhawk/storage/blackhole_list.txt" if DOCKER else "./storage/blackhole_list.txt")

"""DICOM files storage"""

DICOM_STORAGE_DIR = "/opt/dicomhawk/storage/dicom_storage" if DOCKER else "./storage/dicom_storage"

"""DICOM files recieved through the server storage"""

C_STORE_STORAGE = "/opt/dicomhawk/storage/c_store_files" if DOCKER else "./storage/c_store_files"

"""DICOM server port configuration"""
try:
    DICOM_PORTS = json.loads(os.getenv("DICOM_PORTS", "[11112]"))
except json.JSONDecodeError:
    DICOM_PORTS = [11112]

# DICOM Implementation Name
IMPLEMENTATION_NAME = os.getenv("DICOM_IMPLEMENTATION_NAME", "ORTHANC")
IMPLEMENTATION_UID = os.getenv("DICOM_IMPLEMENTATION_UID", "1.2.826.0.1.3680043.9.3811.2.0.1")

"""DICOM server host ip configuration"""
DICOM_SERVER_HOST = "172.29.0.3" if DOCKER else "0.0.0.0"


"""Redis host configuration"""
REDIS_HOST = os.getenv("REDIS_HOST", "172.29.0.4") if DOCKER else "localhost"

"""Logs directories"""
MAIN_LOG_DIRECTORY, SIMPLIFIED_LOG_DIRECTORY, EXCEPTIONS_LOG_DIRECTORY = (
    ("/var/log/dicomhawk/pynetdicom", "/var/log/dicomhawk/simplified", "/var/log/dicomhawk/exceptions")
    if DOCKER
    else (
        "../flask_logging_server/logs/pynetdicom",
        "../flask_logging_server/logs/simplified",
        "./exceptions",
    )
)

"""The sqlite file path"""
DICOM_DATABASE = "/opt/dicomhawk/db/db.db" if DOCKER else "./storage/db.db"

"""TCIA username and password to use it in API calls"""
TCIA_USER_NAME = os.getenv("TCIA_USER_NAME", "user")
TCIA_PASSWORD = os.getenv("TCIA_PASSWORD", "pass")

"""Time unit to schedule tcia files retrieval"""
TCIA_PERIOD_UNIT = os.getenv("TCIA_PERIOD_UNIT", "week")


"""Default update dicom files from tcia API each 1 week"""
TCIA_PERIOD = int(os.getenv("TCIA_PERIOD", 1))

"""The path where TCIA dicom files save on retrieval"""
TCIA_FILES_DIRECTORY = "/opt/dicomhawk/tcia/data" if DOCKER else "./storage/tcia_data"
"""Files stagger directory"""
TCIA_FILES_STAGGER_DIRECTORY = "/opt/dicomhawk/tcia/stagger" if DOCKER else "./storage/stagger"
""" API key Abuseipdb """
ABUSE_IP_API_KEY = os.getenv(
    "ABUSE_IP__KEY",
    "apikey",
)

""" API key Abuseipdb """
IP_QUALITY_SCORE_API_KEY = os.getenv(
    "IP_QUALITY_SCORE_API_KEY",
    "apikey",
)

""" API key Virus Total """
VIRUS_TOTAL_API_KEY = os.getenv(
    "VIRUS_TOTAL_API_KEY",
    "apikey",
)

"""Canary pdf path"""
CANARY_PDF_PATH = "/opt/dicomhawk/storage/can.pdf" if DOCKER else "./storage/can.pdf"


"""TCIA activated"""
TCIA_ACTIVATED = os.getenv("TCIA_ACTIVATED", "True").lower() in TRUE_LIST


""" Modalities of the studies should be retrieved from TCIA """
MODALITIES = json.loads(os.getenv("MODALITIES", '["CT", "MR", "US", "DX"]'))

"""Minimum number of files in each serie retrieved from The Cancer Imaging Archeive API"""
MINIMUM_TCIA_FILES_IN_SERIE = int(os.getenv("MINIMUM_TCIA_FILES_IN_SERIE", 1))

"""Maximum number of files in each serie retrieved from The Cancer Imaging Archeive API"""

MAXIMUM_TCIA_FILES_IN_SERIE = int(os.getenv("MAXIMUM_TCIA_FILES_IN_SERIE", 3))

"""Number of studies for each modality from TCIA"""
TCIA_STUDIES_PER_MODALITY = int(os.getenv("TCIA_STUDIES_PER_MODALITY", 10))

"""Honeytoken URL"""
HONEY_URL = os.getenv("HONEY_URL","VALUE")

"""Activate DICOM files integrity checks every 6 hours"""
INTEGRITY_CHECK = os.getenv("INTEGRITY_CHECK", "True").lower() in TRUE_LIST

"""Integrity checker file storage path"""
HASH_STORAGE_PATH = "/opt/dicomhawk/storage/hash_store.json" if DOCKER else "./storage/hash_store.json"

"""Faker locale for generating patient names and data"""
FAKER_LOCALE = os.getenv("FAKER_LOCALE", "en_US")

"""OpenStreetMap Integration Configuration"""
OSM_ENABLED = os.getenv("OSM_ENABLED", "True").lower() in TRUE_LIST

"""Country to search for medical institutions (ISO 3166-1 alpha-2 code)"""
OSM_COUNTRY = os.getenv("OSM_COUNTRY", "DK")

"""City to search for medical institutions (optional, searches entire country if not specified)"""
OSM_CITY = os.getenv("OSM_CITY", "")

"""Cache duration for OSM data in hours"""
OSM_CACHE_DURATION = int(os.getenv("OSM_CACHE_DURATION", 24))

"""Maximum number of institutions to fetch from OSM"""
OSM_MAX_INSTITUTIONS = int(os.getenv("OSM_MAX_INSTITUTIONS", 50))

"""Timeout for OSM API requests in seconds"""
OSM_TIMEOUT = int(os.getenv("OSM_TIMEOUT", 30))

"""Fallback institutions when OSM is disabled or fails"""
OSM_FALLBACK_INSTITUTIONS = json.loads(os.getenv("OSM_FALLBACK_INSTITUTIONS", 
    '["Københavns Sundhedscenter", "Aarhus Kliniken", "Odense Patienthus", "Nordjylland Med Institut"]'))

"""OSM cache file path"""
OSM_CACHE_FILE = "/opt/dicomhawk/storage/osm_institutions_cache.json" if DOCKER else "./storage/osm_institutions_cache.json"
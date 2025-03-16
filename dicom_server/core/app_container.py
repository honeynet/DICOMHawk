"""
This module defines the ApplicationContext class using the Dependency Injector library to manage dependencies throughout the start of the application.

The ApplicationContext class acts as a central configuration hub for all services on the server

The setup includes adding the following providers:
 
 Loggers provider
 ------------------
 
 data-access agents:
 ------------------
 
    * dicom_db: Manages the DICOM database service, responsible for storing/retrieving dicom files information maintained at the storage block.
    
    * redis_handler: Implements a redis service responsible for storing of dicom sessions information, provides rapid access for external analysis/visualization middleware.

 TCIA providers:
 ------------------
 
    * tcia_api: Handles communication between the application and The Cancer Imaging Archive API.
    
    * tcia_manager: Handles file exchange by removing old files, organizing new ones, injectting new files with honeytokens and initializing the database. 
    
    * tcia_scheduler: Schedules the retrieval of new DICOM files on a periodic basis.
    
 Threat Intelligence:
 ------------------
 
    * threat_intelligence: provides IP information from three Threat Intelligence services (AbuseIPdatabase, IPQualityScore, and VirusTotal).
 
 Files Integrity Checker:
 ------------------
    * files_checker: Verifies the integrity of the dicom files stored at the dicom storage every seven hours, detecting any unauthorized access attemp.
 
 Network Management:
 ------------------
 
    * blackhole: Null-route the requests with IP addresses that belong to one of the known mass-scanners mitigitating the potential of the heneypot been identified as a honeypot in the future

    * session_collector: Collects and manages data from dicom sessions in order to ensure consistency in data analysis and visualization 
 
 DICOM Services:
 ------------------
 
    * dicom_handler: implements the standard dicom operations, acting as a SCP (Service Provider) on C-Echo and C-Store first part of C-Get, C-Find and C-Move (receiving and responding to requests) operations
        and as a SCU (Serive User) for the second part of C-Get, C-Find and C-Move (initiating requests) operations
 
    * dicom_application: handles the configuration, initialization of the DICOM application entity and starts the DICOM server.
"""

import sys, os

from dependency_injector import containers, providers
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import redis
import config
from loggers import Loggers
from dicomdb import DicomDatabase
from redis_handler import RedisClient
from dicom_session_manager import SessionCollector
from dicom_handlers import DICOMHandlers
from dicom_application import DicomStarter
from threat_intelligence_handler import ThreatIntelligence
import logging
from custom_units.integrity_checker import FilesChecker
from custom_units.tcia_management import TCIAAPI
from custom_units.tcia_management import TCIAManager
from custom_units.tcia_management import TCIAScheduler
from custom_units.network_manager import Blackhole


class ApplicationContainer(containers.DeclarativeContainer):

    # Loggers service
    loggers = providers.Singleton(
        Loggers,
        config.PROD,
        config.MAIN_LOG_DIRECTORY,
        config.SIMPLIFIED_LOG_DIRECTORY,
        config.EXCEPTIONS_LOG_DIRECTORY,
    )

    if config.FLASK_ACTIVATED:
        loggers()
    app_logger = providers.Singleton(logging.getLogger, "app_logger")
    exceptions_logger = providers.Singleton(logging.getLogger, "exceptions")
    simplified_logger = providers.Singleton(logging.getLogger, "simplified_logger")

    # DICOM database session configuration
    engine = providers.Singleton(create_engine, f"sqlite:///{config.DICOM_DATABASE}")
    session_factory = providers.Singleton(sessionmaker, bind=engine)
    session = providers.Singleton(lambda sf: sf(), session_factory)

    # DICOM_database provider
    dicom_db = providers.Singleton(
        DicomDatabase, app_logger, exceptions_logger, config.DICOM_STORAGE_DIR, session
    )

    # Redis provider
    redis_client = providers.Singleton(redis.Redis, config.REDIS_HOST, 6379)
    try:
        redis_client().client_list()
    except ConnectionError:
        print(
            f"No Redis database is running on {config.REDIS_HOST}, port 6379. Please start the Redis service.",
        )
        sys.exit(1)

    redis_handler = providers.Singleton(
        RedisClient, app_logger, exceptions_logger, redis_client
    )

    # TCIA providers

    tcia_api = providers.Factory(
        TCIAAPI,
        app_logger,
        exceptions_logger,
        config.TCIA_USER_NAME,
        config.TCIA_PASSWORD,
        config.MINIMUM_TCIA_FILES_IN_SERIE,
        config.MAXIMUM_TCIA_FILES_IN_SERIE,
        config.MODALITIES,
        config.TCIA_STUDIES_PER_MODALITY,
    )
    tcia_manager = providers.Singleton(
        TCIAManager,
        app_logger,
        exceptions_logger,
        config.HONEY_URL,
        config.DICOM_STORAGE_DIR,
        config.TCIA_FILES_DIRECTORY,
        config.TCIA_FILES_STAGGER_DIRECTORY,
        config.CANARY_PDF_PATH,
        dicom_db,
        redis_handler,
        tcia_api,
    )

    tcia_scheduler = providers.Singleton(
        TCIAScheduler,
        app_logger,
        exceptions_logger,
        config.TCIA_PERIOD,
        config.TCIA_PERIOD_UNIT,
        tcia_manager,
    )

    # IP Threat Intelligence provider
    threat_intelligence = providers.Singleton(
        ThreatIntelligence,
        app_logger,
        exceptions_logger,
        config.ABUSE_IP_API_KEY,
        config.IP_QUALITY_SCORE_API_KEY,
        config.VIRUS_TOTAL_API_KEY,
    )

    # DICOM files integrity cheacker provider
    files_checker = providers.Singleton(
        FilesChecker,
        app_logger,
        exceptions_logger,
        config.DICOM_STORAGE_DIR,
        config.HASH_STORAGE_PATH,
        redis_handler,
    )

    # Blackholee service provider
    blackhole = providers.Singleton(
        Blackhole,
        app_logger,
        exceptions_logger,
        config.BLOCK_SCANNERS,
        config.BLACKHOLE_FILE_PATH,
    )

    # A DICOM connection comprises multiple DICOM requests. A collector service provider is added to manage session information before logging.
    session_collector = providers.Singleton(
        SessionCollector,
        app_logger,
        simplified_logger,
        exceptions_logger,
        redis_handler,
        threat_intelligence,
    )

    # DICOM handlers provider
    dicom_handlers = providers.Singleton(
        DICOMHandlers, app_logger, exceptions_logger, session_collector, dicom_db
    )

    # The DICOM application handles the application entity configuration
    dicom_application = providers.Singleton(
        DicomStarter,
        app_logger,
        exceptions_logger,
        config.DICOM_PORT,
        config.DICOM_SERVER_HOST,
        dicom_handlers,
    )

    if config.TCIA_ACTIVATED:
        tcia_scheduler()

    if config.INTEGRITY_CHECK:
        files_checker()

    if config.BLOCK_SCANNERS:
        blackhole()

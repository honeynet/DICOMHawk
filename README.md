# DICOMHawk

![DICOMHawk Logo](cover_images/dicomhawk_logo.png)

DICOMHawk is a powerful and efficient honeypot for DICOM servers, designed to attract and log unauthorized access attempts and interactions. Built using Flask and pynetdicom, DICOMHawk offers a streamlined web interface for monitoring and managing DICOM interactions in real-time.

# Key Features

- DICOMHawk enables potential attackers to perform DICOM operations on the two standard DICOM information models (STUDYROOT and PATIENTROOT) through its DICOM port.

- DICOMHawk provides an API service enabling attackers to interact with the DICOM server content. Using the API endpoints, an attacker can search and download studies, series, patient and images data. Moreover, they can upload files to the Web API server.

- DICOMHawk stores real DICOM files that are updated periodically through "The Cancer Imaging Archive (TCIA)" API, which metadata as PHI is modified to resemble real patient data of Danish citizens in the Danish settings.

- DICOMHawk supports a dynamic data rotation mechanism that automatically replaces the stored DICOM files with the ones downloaded from TCIA.

- DICOMHawk employs different types of honeytokens in both the DICOM server and the Web API including encapsulated PDF canary tokens, honeyURLs (fake URLs that are seeded into the DICOM datasets), credential honeytokens, hidden endpoints and hidden credentials in the source code.

- DICOMHawk provides automatic threat intelligence chack on each unique IP address interacts with honeypot.

- An optional Blackhole service is integrated to DICOMHawk blocking traffic on kernel level for the known mass-scanner services. To enable it, see _DICOMHawk configuration_.

- DICOMHawk uses centralized monitoring to track attacker activities. It employs an Elastic Stack where Logstash only needs the honeypot's IP address to retrieve staged data from the Redis server for logging and monitoring.

# DICOMHawk Monitoring System

- # Background

  DICOMHawk implements a centralized security monitoring infrastructure designed to track and analyze attacker behavior. It benefits the cybersecurity teams in healthcare and research settings by enabling them to quickly detect security incidents, analyze usage and interaction patterns. It is useful for better understanding the potential attacker techniques, maintaining detailed logs for forensic analysis and tracing the source and impact of each interaction.

  ![DICOMHawk Monitoring System](cover_images/kibana.png)

The monitoring system includes summary metrics and detailed analysis for the received DICOM sessions and API requests, represented with multiple visualization types (numbers, tables, pie charts, timelines). It also includes malicious and abuse scoring as an immediate score on each API request or DICOM session.

The monitoring system utilizes several key components:

1. Logstash: Collects data from Redis database integrated with the honeypot.
2. Elasticsearch: Indexes and stores security events.
3. Kibana: Visualizes the collected data.

# Usage Guide

## DICOMHawk Configuration

config.py, is the configuration file for the honeypot and located in the root directory of the project.
This file contains constants that can be modified to customize the honeypot. Most of these constants including the API keys can be overridden via environment variables, which can be passed through the docker-compose file.

Given that this project is part of a thesis, the current method of passing sensitive information such as API keys through the Docker Compose file is used.
It is important to note that while this method provides ease of configuration, it may not be the most secure. To enhance this security aspect in future, Docker Secrets, Encrypted Configuration Files and other techniques should be used.

To customize the honeypot setup, you can pass environment variables directly through your docker-compose.yml file as shown in this example:

```yaml
services:
  dicom_server:
    environment:
      - PROD=yes
      - FLASK_ACTIVATED=yes
      - BLOCK_SCANNERS=no
      - INTEGRITY_CHECK=yes
      - TCIA_ACTIVATED=yes
      - IP_QUALITY_SCORE_API_KEY=YourIPQualityScoreApiKey
      - VIRUS_TOTAL_API_KEY=YourVirusTotalApiKey
```

## Key Configurable Parameters in `config.py`

The main configurable parameters available in the `config.py` file, along with their possible values are:

### General Configuration

- **PROD**: Boolean (`yes` or `no`) to specify the environment mode. Setting this to `yes` configures the system for production use with corresponding production settings. If `no` is chosen, the system is is set to development mode, which provides debug details, exception details, and more system information.

### TCIA Service Configuration

- **TCIA_USER_NAME**: Username for TCIA API authentication.
- **TCIA_PASSWORD**: Password for TCIA API authentication.
- **TCIA_ACTIVATED**: Boolean (`yes` or `no`) to activate or deactivate TCIA service interaction.
- **TCIA_PERIOD_UNIT**: Unit of time (`day`, `week`, `month`) for updating DICOM files.
- **TCIA_PERIOD**: Frequency of updates, specified numerically.
- **MODALITIES**: List of modalities to retrieve, e.g., `["CT", "MR", "US", "DX"]`.
- **MINIMUM_TCIA_FILES_IN_SERIE**: Minimum number of files per series.
- **MAXIMUM_TCIA_FILES_IN_SERIE**: Maximum number of files per series.

### Logging Configuration

- **FLASK_ACTIVATED**: Boolean to enable (`yes`) or disable (`no`) Flask server logging.

### Integrity Checks and Threat Intelligence

- **INTEGRITY_CHECK**: Boolean to enable (`yes`) or disable (`no`) periodic integrity checks.
- **ABUSE_IP_API_KEY**, **IP_QUALITY_SCORE_API_KEY**, **VIRUS_TOTAL_API_KEY**: API keys for respective threat intelligence services.

### DICOM Server and Blackhole Configuration

- **DICOM_PORT**: Port number for the DICOM server.
- **DICOM_SERVER_HOST**: IP address or hostname of the DICOM server.
- **BLOCK_SCANNERS**: Boolean to block (`yes`) or allow (`no`) known mass scanners.

## Deploying DICOMHawk Using Docker Compose

Before deploying DICOMHawk, be sure that the required ports (104, 3000, 5000, and 6379) are not in use on your system.

**For Linux users**

```bash
netstat -tuln | grep -E '104|3000|5000|6379'
```

**For Windows users**

```bash
Get-NetTCPConnection | Where-Object { $_.LocalPort -eq 104 -or $_.LocalPort -eq 3000 -or $_.LocalPort -eq 5000 -or $_.LocalPort -eq 6379 } | Format-Table
```

If any of these ports are in use, you need to make them available or configure DICOMHawk to use different ports.

### Deployment Architecture

An overview of the deployment process is shown below.
![DICOMHawk Deployment Architecture](cover_images/deployment.png)

To deploy DICOMHawk using Docker Compose, use the following command which clones the repository, navigates to the project directory, and launches the required services. Note that cloning the repository may take a significant amount of time due to the large size of the real DICOM files.

```bash
git clone https://github.com/albab19/ThesisProject2.git
cd ./ThesisProject2/DICOMHawk_thesis_project/
docker-compose up -d
```

## Running DICOMHawk Locally

To run DICOMHawk locally, each service should be run separately in its directory. It is important to ensure that a Redis service is running on port 6379 before starting the other services.

### Pre-requisites

- **Redis Service**: Ensure that Redis is running on port 6379. You can start Redis using the following command if you have Redis installed:

  ```bash
  redis-server --port 6379
  ```

  If you do not have Redis installed, you can easily run a Redis instance using Docker with the following command:

  ```bash
  docker run -p 6379:6379 --name redis-db -d redis
  ```

### Starting Each Service

#### DICOM Server

- Navigate to the DICOM server directory from the project root:

  ```bash
  cd /dicom_server
  ```

- Run the DICOM server using Python:

  ```bash
  python main.py  # Use python3 main.py if your environment defaults to Python 3
  ```

#### API Service

- Navigate to the API service directory from the project root:

  ```bash
  cd /API
  ```

- Run the API using Node.js:

  ```bash
  node app.js
  ```

#### Flask Logging Server

- Navigate to the Flask logging server directory from the project root:

  ```bash
  cd /dicom_server
  ```

- Start the logging server:

  ```bash
  python logserver.py  # Use python3 logserver.py if your environment defaults to Python 3
  ```

## Running the Monitoring Stack

Before deploying monitoring stack, be sure that the required ports (5601, 9200, 9300) are not in use on your system. Follow the same steps to check the DICOMHawk ports and make them available in case they are in use or configure the monitoring stack to use different ports.

To deploy the monitoring stack, navigate to the root directory and run the Docker Compose file which contains the monitoring stack's configurations:

```bash
cd ManagementStack/
docker-compose up -d
```

# Usage Examples

Users can interact with the DICOM server utilizing DCMTK tools and DICOM client applications such as Sante DICOM Viewer, and others.
To download SANTE DICOM viewer: [click on this link](https://santesoft.com/win/sante-dicom-viewer-lite/sante-dicom-viewer-lite.html).

Example commands to interact with the server using DCMTK are:

- Verify the connection to the server using this command.

  ```bash
  echoscu.exe  localhost 11112
  ```

- Find all patients using the PatientRootQueryRetrieveInformationModelFind.

  ```bash
  findscu -v -S -k QueryRetrieveLevel=PATIENT localhost 104
  ```

- Find a specific patient using the PatientRootQueryRetrieveInformationModelFind attributes.

  ```bash
  findscu -v -S -k QueryRetrieveLevel=PATIENT -k PatientName="Jim Madsen" localhost 104
  ```

- Find all studies using the StudyRootQueryRetrieveInformationModelFind.

  ```bash
  findscu -v -S -k QueryRetrieveLevel=STUDY  localhost 104
  ```

- Find a specific study using the StudyRootQueryRetrieveInformationModelFind attributes.

  ```bash
  findscu -v -S -k QueryRetrieveLevel=STUDY -k StudyInstanceUID=1.3.6.1.4.1.14519.5.2.1.6279.6001.142460980973539163820236983184  localhost 104
  ```

- Get a specific study using the StudyRootQueryRetrieveInformationModelGet attributes.

  ```bash
  getscu -v -S -k QueryRetrieveLevel=STUDY -k StudyInstanceUID=1.3.6.1.4.1.14519.5.2.1.6279.6001.142460980973539163820236983184  localhost 104
  ```

Get all studies for a specific patient using the PatientRootQueryRetrieveInformationModelGet attributes.

```bash
getscu -v -S -k QueryRetrieveLevel=PATIENT -k PatientName="Jim Madsen"  localhost 104
```

## Access the Kibana Dashbord

    To access the Kibana dashbord after running the monitoring stack, navigate to "http://localhost:5601/app/dashboards" then click on DICOMHawk.

## Access the Simplified Logging Server

     To access the simplified logging server, navigate to "http://localhost:5000".

## Access the Web API User Interface

     To access the Web API user interface, navigate to "http://localhost:3000" and use username and password is "test" - "test", respectively.

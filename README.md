# DICOMHawk

![DICOMHawk Logo](cover_images/dicomhawk_logo.png)

DICOMHawk is a powerful and efficient honeypot for DICOM servers, designed to attract and log unauthorized access attempts and interactions. Built using Flask and pynetdicom, DICOMHawk offers a streamlined web interface for monitoring and managing DICOM interactions in real-time.

# Key Features

- DICOMHawk enables potential attackers to perform DICOM operations on the two standard DICOM information models (STUDYROOT and PATIENTROOT) through its DICOM port.

- DICOMHawk provides an API service enabling attackers to interact with the DICOM server content. Using the API endpoints, an attacker can search and download studies, series, patient and images data. Moreover, they can upload files to the Web API server.

- DICOMHawk stores real DICOM files that are updated periodically through "The Cancer Imaging Archive (TCIA)" API, which metadata as PHI is modified to resemble real patient data of Danish citizens in the Danish settings.

- DICOMHawk supports a dynamic data rotation mechanism that automatically replaces the stored DICOM files with the ones downloaded from TCIA.
  
- DICOMHawk employs different types of honeytokens in both the DICOM server and the Web API including encapsulated PDF canary tokens, honeyURLs (fake URLs that are seeded into the DICOM datasets), credential honeytokens, hidden endpoints and hidden credentials in the source code.

- DICOMHawk provides automatic threat intelligence checks on each unique IP address that interacts with honeypot.

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

The current method of passing sensitive information such as API keys through the Docker Compose file is used.
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
      - IP_QUALITY_SCORE_API_KEY=APIKEY
      - VIRUS_TOTAL_API_KEY=APIKEY
```

### macOS Users

macOS Control Center binds to TCP port 5000 which prevents the logserver
from starting.

See docs/macos-setup.md for the workaround.

## Key Configurable Parameters in `config.py`

The main configurable parameters available in the `config.py` file, along with their possible values are:

### General Configuration

- **PROD**: Boolean (`yes` or `no`) to specify the environment mode. Setting this to `yes` configures the system for production use with corresponding production settings. If `no` is chosen, the system is is set to development mode, which provides debug details, exception details, and more system information.

### TCIA Service Configuration
TCIA Service should be configured by creating an account for [TCIA](https://www.cancerimagingarchive.net/access-data/) following this link to [create an account](https://wiki.cancerimagingarchive.net/pages/viewpage.action?pageId=23691309). DICOMHawk has a staging mechanism that will update files periodically, when TCIA configurations are enabled. 
The files retrieved from the archive are retrived from publicly available repositories and their licence is saved in 

`
dicom_server/dicom_storage/tcia_data/modality/[StudyInstanceUID]/SeriesInstanceUID/LICENSE
`
.

- **TCIA_USER_NAME**: Username for TCIA API authentication.
- **TCIA_PASSWORD**: Password for TCIA API authentication.
- **TCIA_ACTIVATED**: Boolean (`yes` or `no`) to activate or deactivate TCIA service interaction.
- **TCIA_PERIOD_UNIT**: Unit of time (`day`, `week`, `hour`,`minutes` ) for updating DICOM files periodically.
- **TCIA_PERIOD**: Frequency of updates, specified numerically. For example, if **TCIA_PERIOD_UNIT** is set to `week` and **TCIA_PERIOD** is `2`, the files will be retrieved twice a week.
- **MODALITIES**: List of modalities to retrieve, e.g., `["CT", "MR", "US", "DX"]`. This allows you to choose the modalities you are retrieving for the archive.
- **MINIMUM_TCIA_FILES_IN_SERIE**: Minimum number of files per series.
- **MAXIMUM_TCIA_FILES_IN_SERIE**: Maximum number of files per series.

### Logging Configuration

- **FLASK_ACTIVATED**: Boolean to enable (`yes`) or disable (`no`) Flask server logging.

### Integrity Checks 
- **INTEGRITY_CHECK**: Boolean to enable (`yes`) or disable (`no`) periodic integrity checks on dicom files stored in the honeypot.

### DICOM Server and Blackhole Configuration

- **DICOM_PORT**: Port number for the DICOM server.
- **DICOM_SERVER_HOST**: IP address or hostname of the DICOM server.
- **BLOCK_SCANNERS**: Boolean to block (`yes`) or allow (`no`) known mass scanners.

## Threat Intelligence for the DICOM server and the Web API

To enable IP reputation checks for addresses interacting with DICOMHawk, include the following environment variables in your `docker-compose.yml` file.

Each variable corresponds to a threat intelligence service. You must obtain valid API keys from the respective providers before use:

- **`ABUSE_IP_API_KEY`** – [AbuseIPDB](https://www.abuseipdb.com/)
- **`IP_QUALITY_SCORE_API_KEY`** – [IPQualityScore](https://www.ipqualityscore.com/)
- **`VIRUS_TOTAL_API_KEY`** – [VirusTotal](https://www.virustotal.com/gui/home/upload)

Replace the `APIKEY` value in the `docker-compose.yml` file with the obtained API keys for each service:

**BEFORE**:
```env
ABUSE_IP_API_KEY=APIKEY
IP_QUALITY_SCORE_API_KEY=APIKEY
VIRUS_TOTAL_API_KEY=APIKEY
```

**AFTER**:
```env
ABUSE_IP_API_KEY=THE_KEY_YOU_OBTAINED
IP_QUALITY_SCORE_API_KEY=THE_KEY_YOU_OBTAINED
VIRUS_TOTAL_API_KEY=THE_KEY_YOU_OBTAINED
```

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
git clone https://github.com/honeynet/DICOMHawk.git
cd ./DICOMHawk
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
- **Installing Packages**:
  ```bash
  cd ./dicom_server
  pip install -r .\requirements.txt
  ```

### Starting Each Service

#### DICOM Server

- Navigate to the DICOM server directory from the project root:

  ```bash
  cd ./dicom_server
  ```

- Run the DICOM server using Python:

  ```bash
  python main.py  # Use python3 main.py if your environment defaults to Python 3
  ```

#### API Service

- Navigate to the API service directory from the project root:

  ```bash
  cd ./API
  ```

- Run the API using Node.js:

  ```bash
  node app.js
  ```

#### Flask Logging Server

- Navigate to the Flask logging server directory from the project root:

  ```bash
  cd ./flask_logging_server
  ```

- Start the logging server:

  ```bash
  python logserver.py  # Use python3 logserver.py if your environment defaults to Python 3
  ```

## Running the Monitoring Stack

Before deploying the monitoring stack, be sure that the required ports (5601, 9200, 9300) are not in use on your system. Follow the same steps to check the DICOMHawk ports and make them available in case they are in use or configure the monitoring stack to use different ports.

**For Linux users**

```bash
netstat -tuln | grep -E '5601|9200|9300'
```

**For Windows users**

```bash
Get-NetTCPConnection | Where-Object { $_.LocalPort -eq 5601 -or $_.LocalPort -eq 9200 -or $_.LocalPort -eq 9300 } | Format-Table
```

To deploy the monitoring stack, navigate to the root directory and run the Docker Compose file which contains the monitoring stack's configurations:

```bash
cd monitoring_stack/
docker-compose up -d
```


# Honeytokens
Honeytokens (canary PDFs and honeyURLs) are security measures used to detect and alert on unauthorized access or potential breaches.
## DICOM server
The DICOM server in DICOMHawk is designed to automatically update its DICOM file repository periodically, pulling new files from The Cancer Imaging Archive (TCIA). During this update process, the system injects selected DICOM files with honeytokens, specifically canary PDFs and honeyURLs, as part of its enhanced security measures.

When the DICOM server periodically removes old DICOM files and retrieves new ones from TCIA, the updated canary PDF and honeyURL are automatically injected into some of these new files.
This ensures that the security features are consistently refreshed and tailored to current monitoring and security needs.

### Canary PDFs
Canary PDF files are files that serve as monitored tokens within the DICOM files.

To modify the canary PDF token used, replace the existing **can.pdf** file in the ``dicom_server/storage/can.pdf``directory. The server uses this file as a template for generating canary PDFs injected into new DICOM files retrieved from TCIA.  
Make sure the updated PDF is named **can.pdf** to ensure it is properly recognized and utilized by the system.


### HoneyURLs
HoneyURLs are URLs embedded within DICOM data. When accessed, they indicate potential unauthorized interactions.

The HoneyURL injected into DICOM files can be changed by updating the environment variable **HONEY_URL** in the `docker-compose.yml` file.
Replace ``[YOURHONEYURL]`` with the URL fo your own HoneyURL.

```env
HONEY_URL="https://[YOURHONEYURL]"
```

This change in the environment variable ensures that any new DICOM files automatically fetched and updated by the server will include the new honeyURL.


## Web API
The Web API has also employed four honeytoken types to detect different attack vectors

### robots.txt file and hidden endpoints
Allows an attacker to be misguided and mislead to, for example, the endpoints called: ”/admin”, ”/admin-config”, ”/secure” and ”/ensurance_data”. The purpose of this file is to make the attackers curios to explore the Web API and think of ways to get access to those protected resources. In this way, more meaningful information on attackers' actions can be collected. 
If someone accesses the ”robots.txt” file, the interaction is immediately logged and visualized within the  visualization dashboard which helps identifying potential crawling or scraping activities.

When for example, the ”/admin” endpoint is accessed a fake admin access token is generated, which is not differing in size
from the original access token. This is meant to provide inspiration for the potential adversaries.


### Honey Credentials
Fake credentials appear to be ”leaked” in the login page of the Web API. They are to be found in the raw html source. If these credentials are used by a potential adversary, they are taken to an ”Under development” screen.
Moreover, in order to access the Web API from the very start, the potential adversary has to login into the system. Honey credentials 
”test” - ”test” are used. The
login page is continuously monitored for login attempts and therefore guessing, credential stuffing and brute force attacks can be identified.




# Usage Examples

Users can interact with the DICOM server utilizing DCMTK tools and DICOM client applications such as Sante DICOM Viewer, and others.
To download SANTE DICOM viewer: [click on this link](https://santesoft.com/win/sante-dicom-viewer-lite/sante-dicom-viewer-lite.html).

Example commands to interact with the server using DCMTK are:

- Verify the connection to the server using this command.

  ```bash
  echoscu localhost 104
  ```

- Find all patients.

  ```bash
  findscu -v -S -k QueryRetrieveLevel=PATIENT localhost 104
  ```

- Find a specific patient by their name.

  ```bash
  findscu -v -S -k QueryRetrieveLevel=PATIENT -k PatientName="Jim Madsen" localhost 104
  ```

- Find all studies.

  ```bash
  findscu -v -S -k QueryRetrieveLevel=STUDY  localhost 104
  ```

- Find a specific study by its StudyInstanceUID.

  ```bash
  findscu -v -S -k QueryRetrieveLevel=STUDY -k StudyInstanceUID=1.3.6.1.4.1.14519.5.2.1.6279.6001.142460980973539163820236983184  localhost 104
  ```

- Get a specific study using its StudyInstanceUID.

  ```bash
  getscu -v -S -k QueryRetrieveLevel=STUDY -k StudyInstanceUID=1.3.6.1.4.1.14519.5.2.1.6279.6001.142460980973539163820236983184  localhost 104
  ```

- Get all studies for a specific patient using patient name.

  ```bash
  getscu -v -S -k QueryRetrieveLevel=STUDY -k PatientName="Jim Madsen"  localhost 104
  ```


- Get all studies for a specific patient using patient name.

  ```bash
  getscu -v -S -k QueryRetrieveLevel=STUDY -k PatientName="Jim Madsen"  localhost 104
  ```

- Get all studies for a specific SeriesInstanceUID.
  ```bash
  getscu -v -S -k QueryRetrieveLevel=SERIES -k SeriesInstanceUID=1.3.6.1.4.1.14519.5.2.1.6279.6001.140614242738455943374226148817  localhost 104
  ```

- Store a DICOM file
  ```bash
  storescu -v -d localhost 104 [Path to your DICOM file]
  ```

## Access the Kibana Dashbord

    To access the Kibana dashbord after running the monitoring stack, navigate to "http://localhost:5601/app/dashboards" then click on DICOMHawk.

## Access the Simplified Logging Server

     To access the simplified logging server, navigate to "http://localhost:5000".

## Access the Web API User Interface

     To access the Web API user interface, navigate to "http://localhost:3000" and use username and password is "test" - "test", respectively.






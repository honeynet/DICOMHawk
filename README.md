# DICOMHawk

![DICOMHawk Logo](images/dicomhawk_logo.png)

DICOMHawk is a powerful and efficient honeypot for DICOM servers, designed to attract and log unauthorized access attempts and interactions. Built using Flask and pynetdicom, DICOMHawk offers a streamlined web interface for monitoring and managing DICOM interactions in real-time.

## Features

- **DICOM Server Simulation**: Supports C-ECHO, C-FIND, and C-STORE operations to simulate a realistic DICOM server environment.
- **Logging**: Detailed logging of DICOM associations, DIMSE messages, and event-specific data to track and analyze potential attacks.
- **Web Interface**: A user-friendly web interface to view server status, active associations, and logs.
- **Custom Handlers**: Easily extendable to support additional DICOM services and custom logging or handling requirements.

## Getting Started

### Prerequisites

- Docker and Docker Compose installed on your machine
- DCMTK tools installed on your local machine for testing

### Installation

1. **Clone the repository**:

    ```bash
    git clone https://github.com/honeynet/DICOMHawk.git
    cd dicomhawk
    ```

2. **Start the services with Docker Compose**:

    ```bash
    docker-compose up -d
    ```

    This command starts the Flask application and a log server in detached mode. The web interface is accessible on port 5000, and the DICOM server listens on port 11112. Alternatively, port 104 is also applicable for DICOM (ACR-NEMA).

### Usage

1. **Access the Web Interface**:

    Open a web browser and go to `http://127.0.0.1:5000` to access the DICOMHawk web interface. Here, you can monitor server status, view active associations, and check the logs.

    ![DICOMHawk Web Interface](images/interface_screenshots.jpg)

2. **Test the DICOM Server**:

    Use DCMTK tools to interact with the DICOM server.

    - **C-ECHO (DICOM Echo Test)**:

        ```bash
        echoscu 127.0.0.1 11112
        ```

    - **C-FIND (DICOM Find Test)**:

        Create a query file, `query.dcm`, with the following content:

        ```plaintext
        (0008,0052) CS [STUDY]                            # QueryRetrieveLevel
        (0010,0010) PN [Baggins^Frodo]                         # Patient's Name
        ```

        Run the C-FIND command:

        ```bash
        findscu -v -S -k QueryRetrieveLevel=STUDY -k PatientName=Baggins^Frodo 127.0.0.1 11112
        ```

    - **C-STORE (DICOM Store Test)**:

        To send a DICOM file to the server, use the `storescu` command:

        ```bash
        storescu 127.0.0.1 11112 path/to/your/file.dcm
        ```

### Development

If you want to make changes to DICOMHawk or extend its functionality, modify the source code, then restart the services:

```bash
docker-compose down
docker-compose up -d
```

### Logs and Monitoring

DICOMHawk provides detailed logging to help you monitor and analyze interactions with the DICOM server:

- **Server Logs**: Access logs to see detailed information about DICOM associations and DIMSE messages.
- **Simplified Logs**: View simplified logs for a quick overview of events.

You can view these logs through the web interface or by accessing the log files directly within the log server container.

```bash
docker-compose logs logserver
```

### Troubleshooting

- **Container Not Starting**: Ensure that the ports 5000 and 11112 are not being used by other applications.
- **No Logs**: Verify that the logging directories exist and have the correct permissions.

For more detailed troubleshooting, check the Docker container logs:

```bash
docker-compose logs
```

### License

DICOMHawk is open-source software released under the MIT License. See the LICENSE file for more details.

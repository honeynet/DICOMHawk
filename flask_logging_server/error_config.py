import random
import string
import datetime
import socket
import os
import uuid

def generate_id(length=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def random_recent_timestamp():
    now = datetime.datetime.now()
    hours_ago = random.randint(1, 24)
    timestamp = now - datetime.timedelta(hours=hours_ago, minutes=random.randint(0, 59))
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")

def get_base_config(request, error_code):
    request_id = f"REQ-{generate_id(12)}"
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    client_ip = request.remote_addr if request else "127.0.0.1"
    
    path = request.path if request else "/unknown"
    method = request.method if request else "GET"
    
    error_reference = f"ERR-{generate_id(16)}"
    
    server_version = "3.2.1"
    
    environment = random.choice(["PRODUCTION", "STAGING", "DEVELOPMENT"])
    
    db_status = random.choice(["CONNECTED", "DEGRADED", "RECONNECTING"])
    
    last_check = random_recent_timestamp()
    
    config_path = f"/etc/dicom/conf/dicom_server_{random.choice(['prod', 'stage', 'dev'])}.conf"
    
    modules = ["core", "storage", "query", "network", "security", "audit"]
    active_modules = ", ".join(random.sample(modules, random.randint(3, len(modules))))
    
    system_user = random.choice(["dicom_service", "pacs_admin", "medical_sys", "radiology_svc"])
    
    log_location = f"/var/log/dicom/{random.choice(['error', 'access', 'system', 'security'])}.log"
    
    return {
        "error_code": error_code,
        "request_id": request_id,
        "timestamp": timestamp,
        "client_ip": client_ip,
        "path": path,
        "method": method,
        "error_reference": error_reference,
        "server_version": server_version,
        "environment": environment,
        "db_status": db_status,
        "last_check": last_check,
        "config_path": config_path,
        "active_modules": active_modules,
        "system_user": system_user,
        "log_location": log_location
    }

ERROR_CONFIGS = {
    400: {
        "error_title": "Bad Request",
        "error_message": "The server could not understand the request due to invalid syntax or malformed parameters."
    },
    401: {
        "error_title": "Unauthorized",
        "error_message": "Authentication is required to access this resource. Please check your credentials and try again."
    },
    403: {
        "error_title": "Forbidden",
        "error_message": "You do not have permission to access this resource. Access control validation failed."
    },
    404: {
        "error_title": "Not Found",
        "error_message": "The requested resource could not be found on this server. Please check the URL and try again."
    },
    500: {
        "error_title": "Internal Server Error",
        "error_message": "The server encountered an unexpected condition that prevented it from fulfilling the request."
    },
    503: {
        "error_title": "Service Unavailable",
        "error_message": "The server is currently unable to handle the request due to temporary overloading or maintenance."
    }
}

def get_error_config(request, error_code):
    base_config = get_base_config(request, error_code)
    
    if error_code in ERROR_CONFIGS:
        specific_config = ERROR_CONFIGS[error_code]
    else:
        specific_config = {
            "error_title": "Unknown Error",
            "error_message": f"An unknown error with code {error_code} occurred."
        }
    
    return {**base_config, **specific_config}

def log_error_page_interaction(error_code, request_id, client_ip, time_spent):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Error page interaction: Code={error_code}, Request={request_id}, IP={client_ip}, Time spent={time_spent}s"
    
    print(f"Logging error page interaction: {log_entry}")
    
    return True

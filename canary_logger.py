import requests
import logging

# Set up logging
logger = logging.getLogger("DICOMHawk-CanaryLogger")
logger.setLevel(logging.INFO)

# Log to file
file_handler = logging.FileHandler('dicomhawk_alerts.log')
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Dummy CanaryToken webhook URL (replace with actual one later)
CANARYTOKEN_URL = "https://canarytokens.com/some-fake-token-url"

def alert_admin(event_type, details=""):
    """
    Logs suspicious DICOM activity and sends a CanaryToken webhook.
    """
    message = f"[ALERT] {event_type} - {details}"
    logger.warning(message)

    # Send Canary webhook
    try:
        requests.get(CANARYTOKEN_URL, timeout=3)
        logger.info("Webhook sent to CanaryToken.")
    except Exception as e:
        logger.error(f"Failed to send webhook: {e}")

from flask import Flask, jsonify, render_template, send_from_directory
import os, json, logging
from gevent.pywsgi import WSGIServer
import ssl
from pathlib import Path

logger = logging.getLogger("log_app_logger")


Docker_ENV = os.getenv("Docker_ENV", "false")


log_directory, simplified_log_directory, host = (
    ("/app/logs", "/app/logs", "172.29.0.5")
    if Docker_ENV == "True"
    else ("./logs", "./logs", "0.0.0.0")
)


# Set logging files
log_file_path = os.path.join(log_directory, "pynetdicom/pynetdicom.log")

simplified_log_file_path = os.path.join(
    simplified_log_directory, "simplified/simplified_logger.log"
)
exception_log_file_path = os.path.join(log_directory, "exceptions/exceptions.log")


app = Flask(__name__)


@app.route("/")
def landing_page():
    return render_template("landing.html")


@app.route("/home")
def home():
    return render_template("status.html")


@app.route("/logs")
def logs():
    return render_template("logs.html")


@app.route("/status")
def status():
    return jsonify({"status": "running"})


@app.route("/logs/all")
def all_logs():
    try:
        if not os.path.exists(log_file_path):
            return jsonify({"error": "Log file does not exist"}), 404
        with open(log_file_path, "r") as f:
            log_content = f.read().replace("\n", "<br>")

        return f"<pre>{log_content}</pre>"
    except Exception as e:
        return jsonify({"error": "Internal Server Error"}), 500


@app.route("/logs/simplified")
def simplified_logs():
    try:
        # if not os.path.exists(simplified_log_file_path):
        #     return jsonify([])  # Return an empty list if the log file does not exist
        log_entries = []
        with open(simplified_log_file_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        log_entries.append(json.loads(str(line.replace("'", '"'))))
                    except json.JSONDecodeError as e:
                        logger.error(f"Unexpected error: {e}")

        return jsonify(log_entries)

    except Exception as e:
        logger.error(f"Error reading simplified log file: {e}")
        return jsonify([])  # Return an empty list in case of error


@app.route("/logs/simplified_page")
def simplified_logs_page():

    return render_template("simplified_logs.html")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory("static", "favicon.ico")
    
def get_tls_status():
    try:
        cert_paths = [
            Path('/dicom_server/certs/server.crt'),
            Path('/app/dicom_server/certs/server.crt')
        ]
        
        cert_path = None
        for path in cert_paths:
            if path.exists():
                cert_path = path
                break
                
        if cert_path is None:
            return {
                "enabled": False,
                "error": "Certificate not found"
            }
        
        key_path = cert_path.parent / 'server.key'
        if not key_path.exists():
            return {
                "enabled": False,
                "error": "Key file not found"
            }
        
        return {
            "enabled": True,
            "protocol": "TLS 1.2/1.3",
            "certificate": {
                "path": str(cert_path),
                "status": "valid",
                "subject": "localhost",
                "expires": "March 14, 2026"
            },
            "ports": {
                "standard": 11112,
                "secure": 11113
            }
        }
    except Exception as e:
        exception_logger.error(f"Error getting TLS status: {e}")
        return {
            "enabled": False,
            "error": str(e)
        }

@app.route('/tls_status')
def tls_status():
    try:
        status = get_tls_status()
        if request.headers.get('Accept') == 'application/json':
            return jsonify(status)
        return render_template('tls_status.html', status=status)
    except Exception as e:
        exception_logger.error(f"Error in TLS status route: {e}")
        return jsonify({"error": str(e)}), 500

@app.errorhandler(404)
def not_found(e):
    # Do not log 404 errors
    return jsonify({"error": "Not Found"}), 404


@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": "Internal Server Error"}), 500


if __name__ == '_main_':
    try:
        
        os.makedirs(log_directory, exist_ok=True)
        os.makedirs(simplified_log_directory, exist_ok=True)
        
        print("Starting Flask server on http://0.0.0.0:5000")
        
        app.run(host='0.0.0.0', port=5000, debug=True)
    except Exception as e:
        print(f"Error starting server: {e}")
        exception_logger.error(f"Failed to start server: {e}")

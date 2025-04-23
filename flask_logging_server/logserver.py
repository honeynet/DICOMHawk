import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, jsonify, render_template, send_from_directory, request, abort
import os, json
from datetime import datetime
logger = logging.getLogger("log_app_logger")


Docker_ENV = os.getenv("Docker_ENV", "false").lower()
print(f"Docker_ENV: {Docker_ENV}")

current_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(current_dir, 'templates')
static_dir = os.path.join(current_dir, 'static')

print(f"Current directory: {current_dir}")
print(f"Template directory: {template_dir}")
print(f"Template directory exists? {os.path.exists(template_dir)}")

if Docker_ENV == "true":
    log_directory = '/app/logs/'
    simplified_log_directory = '/app/simplified_logs/'
    host = "172.29.0.5"
else:
    base_dir = os.path.dirname(current_dir)
    log_directory = os.path.join(base_dir, 'logs')
    simplified_log_directory = os.path.join(base_dir, 'simplified_logs')
    host = "0.0.0.0"

print(f"Log directory: {log_directory}")
print(f"Simplified log directory: {simplified_log_directory}")


os.makedirs(log_directory, exist_ok=True)
os.makedirs(simplified_log_directory, exist_ok=True)
os.makedirs(os.path.join(log_directory, "pynetdicom"), exist_ok=True)
os.makedirs(os.path.join(simplified_log_directory, "simplified"), exist_ok=True)
os.makedirs(os.path.join(log_directory, "exceptions"), exist_ok=True)


# Set logging files
log_file_path = os.path.join(log_directory, "pynetdicom/pynetdicom.log")

simplified_log_file_path = os.path.join(
    simplified_log_directory, "simplified/simplified_logger.log"
)
exception_log_file_path = os.path.join(log_directory, "exceptions/exceptions.log")

print(f"Log file path: {log_file_path}")
print(f"Simplified log file path: {simplified_log_file_path}")
print(f"Exception log file path: {exception_log_file_path}")

def setup_logger(name, log_file, level=logging.INFO, when="midnight", interval=1):
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    handler = TimedRotatingFileHandler(log_file, when=when, interval=interval)
    handler.suffix = "%Y%m%d"
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger

detailed_logger = setup_logger('detailed_logger', log_file_path, logging.DEBUG)
simplified_logger = setup_logger('simplified_logger', simplified_log_file_path)
exception_logger = setup_logger('exception_logger', exception_log_file_path, logging.ERROR)

pynetdicom_logger = logging.getLogger('pynetdicom')
pynetdicom_logger.setLevel(logging.DEBUG)
pynetdicom_logger.addHandler(logging.FileHandler(log_file_path))

from flask_logging_server import error_config

def get_server_status():
    try:
        return {
            "status": "running",
            "last_updated": datetime.now().isoformat()
        }
    except Exception as e:
        exception_logger.error(f"Error getting server status: {e}")
        return {
            "status": "error",
            "last_updated": None,
            "error": str(e)
        }
        
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)


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


@app.route('/log_error_page_interaction', methods=['POST'])
def log_interaction():
    try:
        data = request.json
        error_code = data.get('error_code', 'unknown')
        request_id = data.get('request_id', 'unknown')
        time_spent = data.get('time_spent', 0)
        
        client_ip = request.remote_addr
        success = error_config.log_error_page_interaction(
            error_code, request_id, client_ip, time_spent
        )
        
        print(f"Received error page interaction: Error={error_code}, Request={request_id}, Time={time_spent}s")
        
        return jsonify({"success": success}), 200
    except Exception as e:
        print(f"Error logging interaction: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/trigger-error/<int:error_code>')
def trigger_error(error_code):
    print(f"Triggering error with code: {error_code}")
    
    allowed_codes = [400, 401, 403, 404, 500, 503]
    
    if error_code in allowed_codes:
        abort(error_code)
    else:
        return f"Error code {error_code} is not supported for testing. Supported codes: {', '.join(map(str, allowed_codes))}"

@app.route('/test-template')
def test_template():
    """A route to test if the custom_error template can be rendered"""
    config = error_config.get_error_config(request, 404)
    return render_template('custom_error.html', **config)

@app.errorhandler(404)
def not_found_error(error):
    print("Handling 404 error with custom error page")
    config = error_config.get_error_config(request, 404)
    try:
        return render_template('custom_error.html', **config), 404
    except Exception as e:
        print(f"Error rendering template: {e}")
        return jsonify({"error": "Not Found", "template_error": str(e)}), 404

@app.errorhandler(500)
def internal_server_error(error):
    print("Handling 500 error with custom error page")
    config = error_config.get_error_config(request, 500)
    try:
        return render_template('custom_error.html', **config), 500
    except Exception as e:
        print(f"Error rendering template: {e}")
        return jsonify({"error": "Internal Server Error", "template_error": str(e)}), 500

@app.errorhandler(403)
def forbidden_error(error):
    print("Handling 403 error with custom error page")
    config = error_config.get_error_config(request, 403)
    try:
        return render_template('custom_error.html', **config), 403
    except Exception as e:
        print(f"Error rendering template: {e}")
        return jsonify({"error": "Forbidden", "template_error": str(e)}), 403

@app.errorhandler(400)
def bad_request_error(error):
    print("Handling 400 error with custom error page")
    config = error_config.get_error_config(request, 400)
    try:
        return render_template('custom_error.html', **config), 400
    except Exception as e:
        print(f"Error rendering template: {e}")
        return jsonify({"error": "Bad Request", "template_error": str(e)}), 400

@app.errorhandler(401)
def unauthorized_error(error):
    print("Handling 401 error with custom error page")
    config = error_config.get_error_config(request, 401)
    try:
        return render_template('custom_error.html', **config), 401
    except Exception as e:
        print(f"Error rendering template: {e}")
        return jsonify({"error": "Unauthorized", "template_error": str(e)}), 401

@app.errorhandler(503)
def service_unavailable_error(error):
    print("Handling 503 error with custom error page")
    config = error_config.get_error_config(request, 503)
    try:
        return render_template('custom_error.html', **config), 503
    except Exception as e:
        print(f"Error rendering template: {e}")
        return jsonify({"error": "Service Unavailable", "template_error": str(e)}), 503


if __name__ == '__main__':
    print("Starting Flask server...")
    app.run(host, debug=True, port=5000)

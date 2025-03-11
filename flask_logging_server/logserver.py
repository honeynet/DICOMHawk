import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, jsonify, render_template, send_from_directory, request
import os
import json
from datetime import datetime

# Set paths for log files
log_directory = '/app/logs/'
simplified_log_directory = '/app/simplified_logs/'

# Set logging files
log_file_path = os.path.join(log_directory, 'dicom_server.log')
simplified_log_file_path = os.path.join(simplified_log_directory, 'dicom_simplified.log')
exception_log_file_path = os.path.join(log_directory, 'exception.log')




# Logger setup function
def setup_logger(name, log_file, level=logging.INFO, when="midnight", interval=1):
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



# Ensure that pynetdicom messages are captured
pynetdicom_logger = logging.getLogger('pynetdicom')
pynetdicom_logger.setLevel(logging.DEBUG)
pynetdicom_logger.addHandler(logging.FileHandler(log_file_path))



app = Flask(__name__)

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
@app.route('/')
def landing_page():
  
    return render_template('landing.html')

@app.route('/home')
def home():
    try:
        server_status = get_server_status()
        return render_template('home.html', 
                         status=server_status["status"],
                         last_updated=server_status["last_updated"])
    except Exception as e:
        exception_logger.error(f"Error in home route: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/logs')
def logs():
    return render_template('logs.html')

@app.route('/status')
def status():
    try:
        server_status = get_server_status()
        if request.headers.get('Accept') == 'application/json':
            return jsonify(server_status)
        return render_template('status.html', 
                             status=server_status["status"],
                             last_updated=server_status["last_updated"])
    except Exception as e:
        exception_logger.error(f"Error in status route: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/logs/all')
def all_logs():
    try:
        if not os.path.exists(log_file_path):
            return jsonify({"error": "Log file does not exist"}), 404
        with open(log_file_path, 'r') as f:
            log_content = f.read().replace('\n', '<br>')
        return f"<pre>{log_content}</pre>"
    except Exception as e:
        exception_logger.error(f"Error reading log file: {e}")
        return jsonify({"error": "Internal Server Error"}), 500

@app.route('/logs/simplified')
def simplified_logs():
    try:
        if not os.path.exists(simplified_log_file_path):
            return jsonify([])  # Return an empty list if the log file does not exist
        log_entries = []
        with open(simplified_log_file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        log_entries.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        exception_logger.error(f"Invalid JSON in log file: {line} - {e}")
                        continue
        return jsonify(log_entries)
    except Exception as e:
        exception_logger.error(f"Error reading simplified log file: {e}")
        return jsonify([])  # Return an empty list in case of error

@app.route('/logs/simplified_page')
def simplified_logs_page():
    return render_template('simplified_logs.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico')

@app.errorhandler(404)
def not_found(e):
    # Do not log 404 errors
    return jsonify({"error": "Not Found"}), 404

@app.errorhandler(Exception)
def handle_exception(e):
    exception_logger.error(f"Unhandled exception: {e}")
    return jsonify({"error": "Internal Server Error"}), 500

app.run(host='172.29.0.2',debug=True,port=5000)

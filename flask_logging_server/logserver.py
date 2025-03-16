from flask import Flask, jsonify, render_template, send_from_directory
import os, json, logging

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


@app.errorhandler(404)
def not_found(e):
    # Do not log 404 errors
    return jsonify({"error": "Not Found"}), 404


@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": "Internal Server Error"}), 500


if Docker_ENV != "True":
    app.run(host, debug=True, port=5000)

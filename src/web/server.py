import os
import ujson
from flask import Flask, jsonify, render_template, send_from_directory

import logging

logger = logging.getLogger(__name__)

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


@app.route("/logs/simplified")
def simplified_logs():
    logs_dir = app.config["logs"]
    fpath = os.path.join(logs_dir, "simplified.log")

    if not os.path.exists(fpath):
        return jsonify({"error": "Logs not found"}), 404

    records = []
    with open(fpath, "r") as f:
        for line in f:
            line = line.replace("'", '"').strip()
            if not line:
                continue

            record = ujson.loads(line)
            records.append(record)

    return jsonify(records)
    
@app.route("/logs/all")
def all_logs():
    logs_dir = app.config["logs"]
    fpath = os.path.join(logs_dir, "logs.log")

    if not os.path.exists(fpath):
        return jsonify({"error": "Logs not found"}), 404
    
    with open(fpath, "r") as f:
        log_content = f.read().replace("\n", "<br>")

    return f"<pre>{log_content}</pre>"

def main(
        host: str="0.0.0.0", 
        port: int=5000, 
        logs_dir: str="logs/dicomhawk", 
    ):
    app.config["logs"] = logs_dir
    app.run(host, port=port)

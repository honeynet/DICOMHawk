from flask import Flask, request
from canary_logger import alert_admin

app = Flask(__name__)

@app.route("/")
def home():
    return "DICOMHawk CanaryLogger is active!"

@app.route("/upload", methods=["POST"])
def upload():
    data = request.data
    # Simulate detecting a suspicious upload
    alert_admin("Suspicious Upload", f"Data length: {len(data)} bytes")
    return "Upload received and logged!"

if __name__ == "__main__":
    app.run(debug=True)

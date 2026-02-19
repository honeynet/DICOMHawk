import sys, os

with open("logo.txt", "r") as f:
    print("\033[92m", f.read())
sys.path.append(os.path.abspath("./core/"))
from app_container import ApplicationContainer


app = ApplicationContainer()
app.dicom_application().start_the_application()

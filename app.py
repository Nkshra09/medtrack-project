from flask import Flask, render_template, request, redirect, session
import boto3
import uuid
import logging
import json
import os

app = Flask(__name__)
app.secret_key = "medtrack_secret_key"

# ----------------------------
# Logging Setup
# ----------------------------
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ============================================================
#  👇 CHANGE THIS ONE LINE TO SWITCH MODES
#     LOCAL_MODE = True  → runs locally using JSON files
#     LOCAL_MODE = False → runs on AWS using DynamoDB + SNS
# ============================================================
LOCAL_MODE =False

REGION        = "us-east-1"
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:145023131777:MedTrack"

# ----------------------------
# Local JSON Files
# ----------------------------
USERS_FILE        = "local_users.json"
APPOINTMENTS_FILE = "local_appointments.json"

# ----------------------------
# Local Helper Functions
# ----------------------------
def _read(file):
    if not os.path.exists(file):
        return []
    with open(file, "r") as f:
        return json.load(f)

def _write(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

# ----------------------------
# Local Table Class
# (mimics DynamoDB behaviour)
# ----------------------------
class LocalTable:
    def __init__(self, file, key):
        self.file = file
        self.key  = key

    def put_item(self, Item):
        data = _read(self.file)
        data = [d for d in data if d.get(self.key) != Item.get(self.key)]
        data.append(Item)
        _write(self.file, data)

    def get_item(self, Key):
        data     = _read(self.file)
        key_name = list(Key.keys())[0]
        key_val  = list(Key.values())[0]
        item     = next((d for d in data if d.get(key_name) == key_val), None)
        return {"Item": item} if item else {}

    def update_item(self, Key, UpdateExpression,
                    ExpressionAttributeValues, ExpressionAttributeNames=None):
        data     = _read(self.file)
        key_name = list(Key.keys())[0]
        key_val  = list(Key.values())[0]
        for item in data:
            if item.get(key_name) == key_val:
                if "login_count" in UpdateExpression:
                    item["login_count"] = item.get("login_count", 0) + 1
                if "diagnosis" in UpdateExpression:
                    item["diagnosis"] = ExpressionAttributeValues.get(":d", "")
                    item["status"]    = ExpressionAttributeValues.get(":status", "Completed")
        _write(self.file, data)

    def scan(self):
        return {"Items": _read(self.file)}

# ----------------------------
# Local SNS Class
# (just prints instead of sending)
# ----------------------------
class LocalSNS:
    def publish(self, TopicArn, Message, Subject):
        logging.info(f"[LOCAL SNS] {Subject}: {Message}")
        print(f"\n📧 SNS NOTIFICATION → {Subject}\n   {Message}\n")

# ----------------------------
# Connect to LOCAL or AWS
# ----------------------------
if LOCAL_MODE:
    users_table        = LocalTable(USERS_FILE,        key="email")
    appointments_table = LocalTable(APPOINTMENTS_FILE, key="appointment_id")
    sns                = LocalSNS()
    print("🟡 LOCAL MODE — using JSON files, no AWS needed")
else:
    dynamodb           = boto3.resource('dynamodb', region_name=REGION)
    users_table        = dynamodb.Table('UsersTable')
    appointments_table = dynamodb.Table('AppointmentsTable')
    sns                = boto3.client('sns', region_name=REGION)
    print("🟢 AWS MODE — connected to DynamoDB + SNS")

# ============================
# ROUTES
# ============================

# ----------------------------
# Home
# ----------------------------
@app.route("/")
def home():
    return render_template("index.html")

# ----------------------------
# Register
# ----------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]

        # Check if email already exists
        existing = users_table.get_item(Key={"email": email})
        if "Item" in existing:
            return render_template("register.html", error="Email already registered!")

        users_table.put_item(
            Item={
                "email":       email,
                "name":        request.form["name"],
                "password":    request.form["password"],
                "role":        request.form["role"],
                "age":         request.form.get("age", ""),
                "blood_type":  request.form.get("blood_type", ""),
                "phone":       request.form.get("phone", ""),
                "login_count": 0
            }
        )
        logging.info(f"New user registered: {email}")
        return redirect("/login")

    return render_template("register.html")

# ----------------------------
# Login
# ----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form["email"]
        password = request.form["password"]

        response = users_table.get_item(Key={"email": email})

        if "Item" in response and response["Item"]["password"] == password:
            user = response["Item"]
            session["user"]       = email
            session["role"]       = user["role"]
            session["name"]       = user["name"]
            session["blood_type"] = user.get("blood_type", "")
            session["age"]        = user.get("age", "")

            users_table.update_item(
                Key={"email": email},
                UpdateExpression="SET login_count = login_count + :val",
                ExpressionAttributeValues={":val": 1}
            )

            logging.info(f"{email} logged in")

            if session["role"] == "doctor":
                return redirect("/doctor_dashboard")
            else:
                return redirect("/patient_dashboard")

        return render_template("login.html", error="Invalid email or password")

    return render_template("login.html")

# ----------------------------
# Logout
# ----------------------------
@app.route("/logout")
def logout():
    logging.info(f"{session.get('user')} logged out")
    session.clear()
    return redirect("/login")

# ----------------------------
# Patient Dashboard
# ----------------------------
@app.route("/patient_dashboard")
def patient_dashboard():
    if "user" not in session or session.get("role") != "patient":
        return redirect("/login")
    return render_template("patient_dashboard.html")

# ----------------------------
# Doctor Dashboard
# ----------------------------
@app.route("/doctor_dashboard")
def doctor_dashboard():
    if "user" not in session or session.get("role") != "doctor":
        return redirect("/login")
    return render_template("doctor_dashboard.html")

# ----------------------------
# Book Appointment
# ----------------------------
@app.route("/book_appointment", methods=["GET", "POST"])
def book_appointment():
    if "user" not in session:
        return redirect("/login")

    if request.method == "POST":
        appointment_id = str(uuid.uuid4())

        appointments_table.put_item(
            Item={
                "appointment_id": appointment_id,
                "patient_email":  session["user"],
                "patient_name":   session.get("name", ""),
                "doctor_email":   request.form["doctor_email"],
                "date":           request.form["date"],
                "time":           request.form["time"],
                "reason":         request.form.get("reason", ""),
                "status":         "Scheduled"
            }
        )

        # SNS Notification
        try:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=f"New appointment booked by {session['user']} "
                        f"on {request.form['date']} at {request.form['time']}",
                Subject="New Appointment - MedTrack"
            )
            logging.info("SNS notification sent")
        except Exception as e:
            logging.warning(f"SNS failed: {e}")

        logging.info(f"Appointment booked by {session['user']}")
        return redirect("/view_appointment_patient")

    return render_template("book_appointment.html")

# ----------------------------
# View Patient Appointments
# ----------------------------
@app.route("/view_appointment_patient")
def view_appointment_patient():
    if "user" not in session:
        return redirect("/login")

    response     = appointments_table.scan()
    appointments = [
        item for item in response.get("Items", [])
        if item.get("patient_email") == session["user"]
    ]
    return render_template("view_appointment_patient.html", appointments=appointments)

# ----------------------------
# View Doctor Appointments
# ----------------------------
@app.route("/view_appointment_doctor")
def view_appointment_doctor():
    if "user" not in session:
        return redirect("/login")

    response     = appointments_table.scan()
    appointments = [
        item for item in response.get("Items", [])
        if item.get("doctor_email") == session["user"]
    ]
    return render_template("view_appointment_doctor.html", appointments=appointments)

# ----------------------------
# Submit Diagnosis
# ----------------------------
@app.route("/submit_diagnosis", methods=["GET", "POST"])
def submit_diagnosis():
    if "user" not in session:
        return redirect("/login")

    if request.method == "POST":
        appointment_id = request.form["appointment_id"]
        diagnosis      = request.form["diagnosis"]

        appointments_table.update_item(
            Key={"appointment_id": appointment_id},
            UpdateExpression="SET diagnosis = :d, #s = :status",
            ExpressionAttributeValues={
                ":d":      diagnosis,
                ":status": "Completed"
            },
            ExpressionAttributeNames={"#s": "status"}
        )

        logging.info(f"Diagnosis submitted for {appointment_id}")
        return redirect("/view_appointment_doctor")

    appointment_id = request.args.get("appointment_id")
    return render_template("submit_diagnosis.html", appointment_id=appointment_id)

# ----------------------------
# Search by Date
# ----------------------------
@app.route("/search", methods=["GET", "POST"])
def search():
    if "user" not in session:
        return redirect("/login")

    if request.method == "POST":
        search_date = request.form["date"]
        response    = appointments_table.scan()
        results     = [
            item for item in response.get("Items", [])
            if item.get("date") == search_date
        ]
        return render_template("search_results.html",
                               appointments=results,
                               search_date=search_date)

    return render_template("search_results.html", appointments=[], search_date="")

# ----------------------------
# Health Check
# ----------------------------
@app.route("/health")
def health():
    return {"status": "Application Running"}, 200

# ----------------------------
# Run App
# ----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

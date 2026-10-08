import csv
import io
import os
import re
import uuid
from datetime import date, datetime, timezone
from functools import wraps
from types import SimpleNamespace
from urllib.parse import urlencode

import qrcode
from dotenv import load_dotenv
from flask import (
    Flask, abort, flash, redirect, render_template, request,
    send_file, session, url_for
)
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "sqlite:///freshers_party.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["EVENT_CAPACITY"] = int(os.getenv("EVENT_CAPACITY", "80"))
app.config["UPI_ID"] = os.getenv("UPI_ID", "akhilbadshah60@ptyes")
app.config["UPI_NAME"] = os.getenv("UPI_NAME", "Freshers Party 2026")

db = SQLAlchemy(app)

PRICES = {"2nd Year": 250, "3rd Year": 350}
YEAR_CAPACITY = {"2nd Year": 50, "3rd Year": 50}
STATUSES = ["Pending", "Verified", "Rejected"]
TICKET_STATUSES = ["Pending", "Active", "Checked In", "Cancelled", "Refunded"]


def utcnow():
    return datetime.now(timezone.utc)


def normalize_year(raw_value):
    value = (raw_value or "").strip()
    if not value:
        return ""
    if " — " in value:
        value = value.split(" — ", 1)[0].strip()
    return value


def valid_usn_for_year(usn, year):
    if year not in PRICES or not usn.strip():
        return False
    return year != "3rd Year" or bool(re.fullmatch(r"4RA2\dAD\d{3}", usn.upper()))


class Student(db.Model):
    __tablename__ = "students"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    usn = db.Column(db.String(40), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20), nullable=False)
    year = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    tickets = db.relationship("Ticket", back_populates="student")


class Event(db.Model):
    __tablename__ = "events"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    venue = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    starts_at = db.Column(db.String(80), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)

    tickets = db.relationship("Ticket", back_populates="event")


class Ticket(db.Model):
    __tablename__ = "tickets"
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.String(40), unique=True, nullable=False, index=True)
    ticket_id = db.Column(db.String(40), unique=True, nullable=True, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    payment_status = db.Column(db.String(20), nullable=False, default="Pending", index=True)
    ticket_status = db.Column(db.String(20), nullable=False, default="Pending", index=True)
    qr_token = db.Column(db.String(80), unique=True, nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    generated_at = db.Column(db.DateTime(timezone=True), nullable=True)

    student = db.relationship("Student", back_populates="tickets")
    event = db.relationship("Event", back_populates="tickets")
    payment = db.relationship("Payment", back_populates="ticket", uselist=False, cascade="all, delete-orphan")
    checkins = db.relationship("Checkin", back_populates="ticket", cascade="all, delete-orphan")


class Payment(db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), unique=True, nullable=False)
    utr = db.Column(db.String(100), nullable=True)
    payment_status = db.Column(db.String(20), nullable=False, default="Pending", index=True)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    verified_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    ticket = db.relationship("Ticket", back_populates="payment")
    verifier = db.relationship("User")


class Checkin(db.Model):
    __tablename__ = "checkins"
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, unique=True, index=True)
    checked_in_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    scanned_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    ticket = db.relationship("Ticket", back_populates="checkins")
    scanner = db.relationship("User")


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="admin")


def verified_count():
    return db.session.scalar(
        select(func.count(Ticket.id)).where(Ticket.payment_status == "Verified")
    ) or 0


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_globals():
    sold = verified_count()
    event_obj = db.session.scalar(select(Event).order_by(Event.id).limit(1))
    capacity = event_obj.capacity if event_obj else app.config["EVENT_CAPACITY"]
    return {
        "capacity": capacity,
        "sold": sold,
        "remaining": max(0, capacity - sold),
        "prices": PRICES,
        "year_capacity": YEAR_CAPACITY,
        "event_details": event_obj,
        "valid_usn_for_year": valid_usn_for_year,
    }


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/event")
def event():
    return render_template("event.html")


@app.route("/book", methods=["GET", "POST"])
def book():
    if request.method == "GET":
        return render_template("book.html")

    name = request.form.get("name", "").strip()
    usn = request.form.get("usn", "").strip().upper()
    phone = request.form.get("phone", "").strip()
    year = normalize_year(request.form.get("year", ""))

    if not all([name, usn, phone, year]):
        flash("All registration fields are required.", "error")
        return render_template("book.html")

    if year not in PRICES:
        flash("Please select a valid year.", "error")
        return render_template("book.html")

    if not valid_usn_for_year(usn, year):
        flash("3rd Year USNs must match the format 4RA2*AD***, for example 4RA24AD001.", "error")
        return render_template("book.html")

    if not phone.isdigit() or len(phone) != 10:
        flash("Enter a valid 10-digit phone number.", "error")
        return render_template("book.html")

    existing = db.session.scalar(select(Student).where(Student.usn == usn))
    if existing:
        flash("This USN is already registered for the event.", "error")
        return render_template("book.html")

    event_obj = db.session.scalar(select(Event).order_by(Event.id).limit(1))
    if not event_obj:
        abort(503, description="Event details have not been initialized.")

    student = Student(name=name, usn=usn, phone=phone, year=year)
    ticket_obj = Ticket(
        booking_id=f"FP26-{uuid.uuid4().hex[:10].upper()}",
        student=student, event=event_obj,
        amount=PRICES[year],
        payment_status="Pending",
        ticket_status="Pending",
    )
    db.session.add(ticket_obj)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This USN is already registered for the event.", "error")
        return render_template("book.html")

    session["pending_booking_id"] = ticket_obj.booking_id
    return redirect(url_for("confirm", booking_id=ticket_obj.booking_id))


@app.route("/confirm/<booking_id>")
def confirm(booking_id):
    ticket_obj = db.session.scalar(select(Ticket).where(Ticket.booking_id == booking_id))
    if not ticket_obj:
        abort(404)
    if not valid_usn_for_year(ticket_obj.student.usn, ticket_obj.student.year):
        flash("This USN is invalid. Correct it on the registration form before continuing.", "error")
        return redirect(url_for("book"))
    return render_template("confirm.html", booking=ticket_obj)


@app.route("/payment/<booking_id>", methods=["GET", "POST"])
def payment(booking_id):
    ticket_obj = db.session.scalar(select(Ticket).where(Ticket.booking_id == booking_id))
    if not ticket_obj:
        abort(404)
    if not valid_usn_for_year(ticket_obj.student.usn, ticket_obj.student.year):
        flash("This USN is invalid. Register again with a valid USN.", "error")
        return redirect(url_for("book"))

    if request.method == "POST":
        utr = request.form.get("utr", "").strip()
        if len(utr) < 4 or len(utr) > 100:
            flash("Enter a valid UTR / Transaction ID.", "error")
            return render_template("payment.html", booking=ticket_obj)

        if ticket_obj.payment_status == "Verified":
            flash("This ticket has already been verified.", "error")
            return redirect(url_for("booking_status", booking_id=ticket_obj.booking_id))

        payment_obj = ticket_obj.payment
        if payment_obj is None:
            payment_obj = Payment(ticket=ticket_obj)
            db.session.add(payment_obj)
        payment_obj.utr = utr
        payment_obj.payment_status = "Pending"
        payment_obj.submitted_at = utcnow()
        payment_obj.verified_at = None
        payment_obj.verified_by = None
        ticket_obj.payment_status = "Pending"
        db.session.commit()
        return redirect(url_for("booking_status", booking_id=ticket_obj.booking_id))

    return render_template("payment.html", booking=ticket_obj)


@app.route("/booking/<booking_id>")
def booking_status(booking_id):
    ticket_obj = db.session.scalar(select(Ticket).where(Ticket.booking_id == booking_id))
    if not ticket_obj:
        abort(404)
    if not valid_usn_for_year(ticket_obj.student.usn, ticket_obj.student.year):
        flash("This USN is invalid. Register again with a valid USN.", "error")
        return redirect(url_for("book"))
    return render_template("status.html", booking=ticket_obj)


@app.route("/ticket/<ticket_id>")
def ticket(ticket_id):
    ticket_obj = db.session.scalar(select(Ticket).where(Ticket.ticket_id == ticket_id))
    if not ticket_obj or ticket_obj.payment_status != "Verified" or not ticket_obj.qr_token:
        abort(404)
    return render_template("ticket.html", ticket=ticket_obj)


@app.route("/ticket-preview")
def ticket_preview():
    event_obj = db.session.scalar(select(Event).order_by(Event.id).limit(1))
    if not event_obj:
        abort(503, description="Event details have not been initialized.")
    preview = SimpleNamespace(
        ticket_id="TKT26-PREVIEW",
        booking_id="FP26-PREVIEW",
        amount=PRICES["3rd Year"],
        payment_status="Verified",
        ticket_status="Active",
        student=SimpleNamespace(name="Sample Student", usn="4RA24AD001", year="3rd Year"),
        event=event_obj,
    )
    return render_template("ticket.html", ticket=preview, preview=True)


@app.route("/qr/<ticket_id>.png")
def qr_image(ticket_id):
    ticket_obj = db.session.scalar(select(Ticket).where(Ticket.ticket_id == ticket_id))
    if not ticket_obj or not ticket_obj.qr_token:
        abort(404)
    payload = url_for("scan", token=ticket_obj.qr_token, _external=True)
    image = qrcode.make(payload)
    out = io.BytesIO()
    image.save(out, format="PNG")
    out.seek(0)
    return send_file(out, mimetype="image/png")


@app.route("/ticket-preview/qr.png")
def ticket_preview_qr():
    image = qrcode.make("Freshers Party 2026 ticket preview - sample only")
    out = io.BytesIO()
    image.save(out, format="PNG")
    out.seek(0)
    return send_file(out, mimetype="image/png")


@app.route("/payment/qr/<booking_id>.png")
def payment_qr_image(booking_id):
    ticket_obj = db.session.scalar(select(Ticket).where(Ticket.booking_id == booking_id))
    if not ticket_obj:
        abort(404)

    payload = "upi://pay?" + urlencode({
        "pa": app.config["UPI_ID"],
        "pn": app.config["UPI_NAME"],
        "am": f"{ticket_obj.amount:.2f}",
        "cu": "INR",
        "tn": f"Freshers Party {ticket_obj.booking_id}",
    })
    qr = qrcode.QRCode(
        version=4,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=12,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white")

    canvas = Image.new("RGB", (560, 560), "#EAF9FF")
    border_color = "#1DC5FF"
    border = Image.new("RGB", (560, 560), border_color)
    inner = Image.new("RGB", (500, 500), "white")
    qr_image = qr_image.resize((430, 430), Image.Resampling.LANCZOS)

    border.paste(inner, (30, 30))
    border.paste(qr_image, (65, 65))
    canvas.paste(border, (0, 0))

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)
    return send_file(out, mimetype="image/png")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin = db.session.scalar(select(User).where(User.username == username))
        if admin and check_password_hash(admin.password_hash, password):
            session["admin_id"] = admin.id
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        flash("Invalid admin username or password.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/admin")
@login_required
def admin_dashboard():
    pending = db.session.scalar(
        select(func.count(Ticket.id)).where(Ticket.payment_status == "Pending")
    ) or 0
    verified = verified_count()
    rejected = db.session.scalar(
        select(func.count(Ticket.id)).where(Ticket.payment_status == "Rejected")
    ) or 0
    checked = db.session.scalar(
        select(func.count(Ticket.id)).where(Ticket.ticket_status == "Checked In")
    ) or 0
    cancelled = db.session.scalar(
        select(func.count(Ticket.id)).where(Ticket.ticket_status.in_(["Cancelled", "Refunded"]))
    ) or 0
    revenue = db.session.scalar(
        select(func.coalesce(func.sum(Ticket.amount), 0))
        .where(Ticket.payment_status == "Verified")
    ) or 0
    students = db.session.scalar(select(func.count(Student.id))) or 0

    rows = db.session.scalars(select(Ticket).order_by(Ticket.created_at.desc())).all()

    year_stats = []
    for year in PRICES:
        reg = db.session.scalar(
            select(func.count(Student.id)).where(Student.year == year)
        ) or 0
        sold = db.session.scalar(
            select(func.count(Ticket.id))
            .join(Student)
            .where(Student.year == year, Ticket.payment_status == "Verified")
        ) or 0
        rev = db.session.scalar(
            select(func.coalesce(func.sum(Ticket.amount), 0))
            .join(Student)
            .where(Student.year == year, Ticket.payment_status == "Verified")
        ) or 0
        year_stats.append((year, reg, sold, rev))

    return render_template(
        "admin_dashboard.html",
        rows=rows, pending=pending, verified=verified, rejected=rejected,
        checked=checked, cancelled=cancelled, revenue=revenue,
        students=students, year_stats=year_stats
    )


@app.post("/admin/booking/<booking_id>/verify")
@login_required
def verify_payment(booking_id):
    ticket_obj = db.session.scalar(
        select(Ticket).where(Ticket.booking_id == booking_id).with_for_update()
    )
    if not ticket_obj:
        abort(404)

    if ticket_obj.payment_status != "Pending":
        flash("This booking is no longer pending.", "error")
        return redirect(url_for("admin_dashboard"))

    if not valid_usn_for_year(ticket_obj.student.usn, ticket_obj.student.year):
        flash("This registration has an invalid USN and cannot be verified.", "error")
        return redirect(url_for("admin_dashboard"))

    payment_obj = ticket_obj.payment
    if not payment_obj or not payment_obj.utr:
        flash("The student must submit a UTR before payment can be verified.", "error")
        return redirect(url_for("admin_dashboard"))

    event_obj = db.session.scalar(
        select(Event).where(Event.id == ticket_obj.event_id).with_for_update()
    )
    if verified_count() >= event_obj.capacity:
        ticket_obj.payment_status = "Rejected"
        payment_obj.payment_status = "Rejected"
        db.session.commit()
        flash("Capacity is full. Payment was not verified.", "error")
        return redirect(url_for("admin_dashboard"))

    year_sold = db.session.scalar(
        select(func.count(Ticket.id))
        .join(Student)
        .where(
            Student.year == ticket_obj.student.year,
            Ticket.payment_status == "Verified",
        )
    ) or 0
    if year_sold >= YEAR_CAPACITY[ticket_obj.student.year]:
        ticket_obj.payment_status = "Rejected"
        payment_obj.payment_status = "Rejected"
        db.session.commit()
        flash(f"{ticket_obj.student.year} capacity is full. Payment was not verified.", "error")
        return redirect(url_for("admin_dashboard"))

    ticket_obj.payment_status = "Verified"
    ticket_obj.ticket_status = "Active"
    ticket_obj.ticket_id = ticket_obj.ticket_id or f"TKT26-{uuid.uuid4().hex[:12].upper()}"
    ticket_obj.qr_token = ticket_obj.qr_token or uuid.uuid4().hex + uuid.uuid4().hex
    ticket_obj.generated_at = utcnow()
    payment_obj.payment_status = "Verified"
    payment_obj.verified_at = utcnow()
    payment_obj.verified_by = session.get("admin_id")

    db.session.commit()
    flash("Payment verified and ticket generated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/booking/<booking_id>/reject")
@login_required
def reject_payment(booking_id):
    ticket_obj = db.session.scalar(
        select(Ticket).where(Ticket.booking_id == booking_id).with_for_update()
    )
    if not ticket_obj:
        abort(404)
    if ticket_obj.payment_status == "Verified":
        flash("A verified payment cannot be rejected here.", "error")
    else:
        payment_obj = ticket_obj.payment or Payment(ticket=ticket_obj)
        payment_obj.payment_status = "Rejected"
        ticket_obj.payment_status = "Rejected"
        db.session.commit()
        flash("Payment rejected.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/ticket/<ticket_id>/cancel")
@login_required
def cancel_ticket(ticket_id):
    ticket_obj = db.session.scalar(select(Ticket).where(Ticket.ticket_id == ticket_id))
    if not ticket_obj:
        abort(404)
    ticket_obj.ticket_status = "Cancelled"
    db.session.commit()
    flash("Ticket cancelled.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/scan")
@login_required
def admin_scan():
    return render_template("scanner.html")


@app.route("/scan/<token>")
def scan(token):
    ticket_obj = db.session.scalar(
        select(Ticket).where(Ticket.qr_token == token).with_for_update()
    )
    if not ticket_obj:
        return render_template("scan_result.html", result="INVALID TICKET")

    if ticket_obj.ticket_status == "Checked In":
        return render_template("scan_result.html", result="TICKET ALREADY USED", ticket=ticket_obj)

    if ticket_obj.ticket_status in ("Cancelled", "Refunded"):
        return render_template("scan_result.html", result="TICKET INVALID / CANCELLED", ticket=ticket_obj)

    if ticket_obj.ticket_status != "Active" or ticket_obj.payment_status != "Verified":
        return render_template("scan_result.html", result="INVALID TICKET", ticket=ticket_obj)

    ticket_obj.ticket_status = "Checked In"
    db.session.add(Checkin(ticket=ticket_obj, scanned_by=session.get("admin_id")))
    db.session.commit()
    return render_template("scan_result.html", result="VALID TICKET", ticket=ticket_obj)


@app.route("/admin/export.csv")
@login_required
def export_csv():
    tickets = db.session.scalars(
        select(Ticket).join(Student).order_by(Ticket.created_at.desc())
    ).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name", "USN", "Phone Number", "Year", "Amount",
        "Booking ID", "Ticket ID", "UTR", "Payment Status",
        "Ticket Status", "Check-in Time"
    ])
    for ticket_obj in tickets:
        checkin = ticket_obj.checkins[-1] if ticket_obj.checkins else None
        writer.writerow([
            ticket_obj.student.name, ticket_obj.student.usn,
            ticket_obj.student.phone, ticket_obj.student.year,
            ticket_obj.amount, ticket_obj.booking_id, ticket_obj.ticket_id or "",
            ticket_obj.payment.utr if ticket_obj.payment else "",
            ticket_obj.payment_status, ticket_obj.ticket_status,
            checkin.checked_in_at.isoformat() if checkin else ""
        ])
    data = io.BytesIO(output.getvalue().encode("utf-8"))
    data.seek(0)
    return send_file(
        data, mimetype="text/csv", as_attachment=True,
        download_name="freshers_party_attendees.csv"
    )


DEFAULT_ADMIN_USERNAME = "PRASHANTH"


def get_admin_credentials():
    username = (os.getenv("ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME) or DEFAULT_ADMIN_USERNAME).strip()
    password = os.getenv("ADMIN_PASSWORD")
    if not password:
        raise RuntimeError("ADMIN_PASSWORD must be set before initializing the database.")
    return username, password


def prepare_legacy_schema():
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    quote = db.engine.dialect.identifier_preparer.quote
    with db.engine.begin() as connection:
        if "bookings" in tables and "legacy_bookings" not in tables:
            if "tickets" in tables and "legacy_tickets" not in tables:
                connection.exec_driver_sql(
                    f"ALTER TABLE {quote('tickets')} RENAME TO {quote('legacy_tickets')}"
                )
            connection.exec_driver_sql(
                f"ALTER TABLE {quote('bookings')} RENAME TO {quote('legacy_bookings')}"
            )
        if "admins" in tables and "legacy_admins" not in tables:
            connection.exec_driver_sql(
                f"ALTER TABLE {quote('admins')} RENAME TO {quote('legacy_admins')}"
            )
        if "legacy_tickets" in inspect(connection).get_table_names():
            for index in inspect(connection).get_indexes("legacy_tickets"):
                connection.exec_driver_sql(f"DROP INDEX {quote(index['name'])}")


def parse_legacy_datetime(value):
    if value is None or isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value)
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def migrate_legacy_data(event_obj):
    table_names = set(inspect(db.engine).get_table_names())

    if "legacy_admins" in table_names:
        for row in db.session.execute(text("SELECT id, username, password_hash, role FROM legacy_admins")):
            if not db.session.scalar(select(User.id).where(User.username == row.username)):
                db.session.add(User(
                    id=row.id,
                    username=row.username,
                    password_hash=row.password_hash,
                    role=row.role,
                ))
        db.session.flush()
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text(
                "SELECT setval(pg_get_serial_sequence('users', 'id'), "
                "COALESCE((SELECT MAX(id) FROM users), 1), "
                "EXISTS (SELECT 1 FROM users))"
            ))

    if "legacy_bookings" not in table_names:
        return

    old_tickets = {}
    if "legacy_tickets" in table_names:
        old_tickets = {
            row.booking_id: row
            for row in db.session.execute(text("SELECT * FROM legacy_tickets"))
        }

    for row in db.session.execute(text("SELECT * FROM legacy_bookings")):
        if db.session.scalar(select(Ticket.id).where(Ticket.booking_id == row.booking_id)):
            continue

        old_ticket = old_tickets.get(row.id)
        created_at = parse_legacy_datetime(row.booking_date) or utcnow()
        old_ticket_status = old_ticket.ticket_status if old_ticket else "Pending"
        old_ticket_id = old_ticket.ticket_id if old_ticket else None
        old_qr_token = old_ticket.qr_token if old_ticket else None
        old_generated_at = parse_legacy_datetime(old_ticket.generated_at) if old_ticket else None
        if row.payment_status == "Verified" and not old_ticket:
            old_ticket_status = "Active"
            old_ticket_id = f"TKT26-{uuid.uuid4().hex[:12].upper()}"
            old_qr_token = uuid.uuid4().hex + uuid.uuid4().hex
            old_generated_at = created_at
        ticket_obj = Ticket(
            booking_id=row.booking_id,
            student_id=row.student_id,
            event_id=event_obj.id,
            amount=row.amount,
            payment_status=row.payment_status,
            ticket_status=old_ticket_status,
            ticket_id=old_ticket_id,
            qr_token=old_qr_token,
            created_at=created_at,
            generated_at=old_generated_at,
        )
        db.session.add(ticket_obj)
        db.session.flush()

        if row.utr or row.payment_status != "Pending":
            db.session.add(Payment(
                ticket=ticket_obj,
                utr=row.utr,
                payment_status=row.payment_status,
                submitted_at=created_at if row.utr else None,
                verified_at=ticket_obj.generated_at if row.payment_status == "Verified" else None,
            ))

        if old_ticket and old_ticket.checkin_at:
            db.session.add(Checkin(
                ticket=ticket_obj,
                checked_in_at=parse_legacy_datetime(old_ticket.checkin_at),
            ))
    db.session.flush()


def init_db():
    with app.app_context():
        prepare_legacy_schema()
        db.create_all()
        event_obj = db.session.scalar(select(Event).order_by(Event.id).limit(1))
        if not event_obj:
            event_obj = Event(
                name="Freshers Party 2026",
                event_date=date(2026, 10, 16),
                venue="RIT Hassan Conference Hall, Main Block",
                location="RIT Hassan, Hassan, Karnataka",
                starts_at="10:00 AM onwards",
                capacity=app.config["EVENT_CAPACITY"],
            )
            db.session.add(event_obj)
            db.session.flush()
        migrate_legacy_data(event_obj)
        pending_tickets = db.session.scalars(
            select(Ticket)
            .join(Student)
            .where(Ticket.payment_status == "Pending")
        ).all()
        for ticket_obj in pending_tickets:
            if not valid_usn_for_year(ticket_obj.student.usn, ticket_obj.student.year):
                ticket_obj.payment_status = "Rejected"
                if ticket_obj.payment:
                    ticket_obj.payment.payment_status = "Rejected"

        username, password = get_admin_credentials()
        existing = db.session.scalar(select(User).where(User.username == username))
        if existing:
            if not check_password_hash(existing.password_hash, password):
                existing.password_hash = generate_password_hash(password)
            db.session.commit()
            return

        db.session.add(User(
            username=username,
            password_hash=generate_password_hash(password),
            role="admin"
        ))
        db.session.commit()


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)

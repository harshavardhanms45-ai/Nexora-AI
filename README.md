# Freshers Party 2026 - Online Ticket Booking System

A Flask ticket booking system with PostgreSQL support.

## Features
- Student registration with unique USN validation; the `4RA2*AD***` format
  (e.g. `4RA24AD001`) is enforced only for 3rd Year students
- Automatic ticket pricing (2nd Year: Rs 250; 3rd Year: Rs 350)
- Event capacity limit of 80, with up to 50 tickets per study year
- UTR submission and admin payment verification/rejection
- Digital ticket generation with a unique entry QR code
- One-time QR check-in
- Revenue and year-wise statistics
- CSV attendee export and booking status page

## Data model
- `users`: administrator accounts
- `students`: student registration details and unique USNs
- `tickets`: event ticket, amount, ticket state, and payment status
- `payments`: UTR and payment verification audit details
- `events`: event name, date, venue, time, and capacity
- `checkins`: one-time QR entry scan records

## Setup

### 1. PostgreSQL
Create a database:
```sql
CREATE DATABASE freshers_party;
```

### 2. Python environment
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment
Copy `.env.example` to `.env` and set `DATABASE_URL` to your PostgreSQL connection
URL. Keep real credentials in `.env` and do not commit them. SQLite is used as a
local fallback only when `DATABASE_URL` is unset.
Set `ADMIN_PASSWORD` to a strong password before starting the application; database
initialization will fail if it is missing.

On startup, existing admin, booking, and ticket records are copied into the
normalized tables. The previous tables are kept with a `legacy_` prefix.

### 4. Run
```bash
python app.py
```
Open http://127.0.0.1:5000

Admin login:
- username: value of `ADMIN_USERNAME`
- password: value of `ADMIN_PASSWORD`

## Event setup notes
- Event date: 16/10/2026
- Venue: RIT Hassan Conference Hall, Main Block
- The payment QR uses configured UPI details; verify these before collecting payments.

For production, use HTTPS, a strong secret, a strong admin password, secure database
credentials, backups, and a production WSGI server.

# freshers-party-event-2026-latest

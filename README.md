# Employee Attendance & Analytics API

An attendance API built with Python, FastAPI, and MongoDB. All application code is in `app/main.py`.

## Features

- Employee creation and listing with pagination.
- Punch-in and punch-out with attendance calculations.
- Manual attendance corrections with an audit trail.
- Employee monthly analytics, department summaries, late leaderboard, and department trends.
- MongoDB query explain plans.

## Requirements

- Python with pip.
- MongoDB 6.0 or newer.

## Setup — Windows PowerShell

From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a local `.env` file containing:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Ensure MongoDB is running. To load the supplied sample data:

```powershell
python sample_seed.py
```

## Run

```powershell
uvicorn app.main:app --port 8000
```

Interactive API documentation: http://127.0.0.1:8000/docs

## Health Check

In another PowerShell terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected response:

```json
{"status": "ok"}
```

## Review and Design Decisions

- `REVIEW.md` documents the starter-code review.
- `DECISIONS.md` explains indexing, concurrency, ties, headcount, and scaling choices.

Do not commit `.env`, secrets, `.venv`, or Python cache files.
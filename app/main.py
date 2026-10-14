"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import json
from pydoc import doc
from bson import json_util
from decimal import Decimal, ROUND_HALF_UP
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import PyMongoError, DuplicateKeyError
load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index("emp_code", unique=True)

    db.employees.create_index(
        [("joined_on", 1), ("department", 1)]
    )
    db.employees.create_index(
        [("department", 1), ("joined_on", 1), ("emp_code", 1)]
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
    )
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)]
    )
    db.attendance_logs.create_index(
        [("status", 1), ("date", -1), ("emp_code", 1)]
    )
    db.attendance_logs.create_index(
        [("emp_code", 1), ("punch_in", -1)]
    )

    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Helpers
def month_bounds(month: str):
    try:
        first_day = date.fromisoformat(f"{month}-01")

        if first_day.month == 12:
            next_month = date(first_day.year + 1, 1, 1)
        else:
            next_month = date(first_day.year, first_day.month + 1, 1)

        last_day = next_month - timedelta(days=1)
        return first_day, last_day

    except ValueError:
        raise HTTPException(422, "month must be a valid YYYY-MM")
def monthly_attendance_pipeline(emp_code: str, first_day: date, last_day: date):
    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": first_day.isoformat(),
                    "$lte": last_day.isoformat(),
                },
            }
        },
        {
            "$set": {
                "is_weekday": {
                    "$in": [
                        {
                            "$dayOfWeek": {
                                "date": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d",
                                        "timezone": "Asia/Kolkata",
                                    }
                                },
                                "timezone": "Asia/Kolkata",
                            }
                        },
                        [2, 3, 4, 5, 6],
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_weekday",
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                ]
                            },
                            {"$cond": ["$half_day", 0.5, 1]},
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
        {"$project": {"_id": 0}},
    ]
def mongo_half_up(value, places: int = 2):
    factor = 10 ** places

    return {
        "$toDouble": {
            "$divide": [
                {
                    "$floor": {
                        "$add": [
                            {
                                "$multiply": [
                                    {"$toDecimal": value},
                                    factor,
                                ]
                            },
                            {"$toDecimal": "0.5"},
                        ]
                    }
                },
                factor,
            ]
        }
    }
def department_summary_pipeline(
    first_day: date,
    last_day: date,
    department: Optional[str] = None,
):
    employee_filter = {
        "joined_on": {"$lte": last_day.isoformat()}
    }
    if department is not None:
        employee_filter["department"] = department

    logs_pipeline = monthly_attendance_pipeline(
        "",
        first_day,
        last_day,
    )

    logs_pipeline[0] = {
        "$match": {
            "date": {
                "$gte": first_day.isoformat(),
                "$lte": last_day.isoformat(),
            },
            "$expr": {"$eq": ["$emp_code", "$$employee_code"]},
        }
    }

    has_work_hours = {
        "$and": [
            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
            {"$isNumber": "$work_hours"},
        ]
    }

    logs_pipeline[2]["$group"].update(
        {
            "work_hours_sum": {
                "$sum": {
                    "$cond": [
                        has_work_hours,
                        {"$toDecimal": "$work_hours"},
                        0,
                    ]
                }
            },
            "work_hours_count": {
                "$sum": {"$cond": [has_work_hours, 1, 0]}
            },
            "on_duty_count": {
                "$sum": {
                    "$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]
                }
            },
        }
    )

    return [
        {"$match": employee_filter},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": logs_pipeline,
                "as": "stats",
            }
        },
        {
            "$unwind": {
                "path": "$stats",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$stats.present_days"},
                "late_count": {"$sum": "$stats.late_count"},
                "total_late_minutes": {"$sum": "$stats.total_late_minutes"},
                "leave_count": {"$sum": "$stats.leave_days"},
                "on_duty_count": {"$sum": "$stats.on_duty_count"},
                "work_hours_sum": {"$sum": "$stats.work_hours_sum"},
                "work_hours_count": {"$sum": "$stats.work_hours_count"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        mongo_half_up(
                            {"$divide": ["$work_hours_sum", "$work_hours_count"]}
                        ),
                        None,
                    ]
                },
            }
        },
        {"$sort": {"department": 1}},
    ]
def late_leaderboard_pipeline(
    first_day: date,
    last_day: date,
    limit: int,
    department: Optional[str] = None,
):
    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": first_day.isoformat(),
                    "$lte": last_day.isoformat(),
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append(
            {"$match": {"employee.department": department}}
        )

    pipeline.extend(
        [
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {
                        "rank": {"$rank": {}}
                    },
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {
                "$project": {
                    "_id": 0,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                    "rank": 1,
                }
            },
        ]
    )

    return pipeline
def daily_department_counts_pipeline():
    return [
        {
            "$match": {
                "$expr": {
                    "$and": [
                        {"$eq": ["$date", "$$day"]},
                        {"$in": ["$emp_code", "$$employee_codes"]},
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {"$cond": ["$half_day", 0.5, 1]},
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
            }
        },
    ]
def department_trend_pipeline(
    department: str,
    date_from: date,
    date_to: date,
):
    start = datetime(
        date_from.year,
        date_from.month,
        date_from.day,
        tzinfo=IST,
    )
    day_count = (date_to - date_from).days + 1

    return [
        {"$match": {"department": department}},
        {
            "$group": {
                "_id": None,
                "employee_codes": {"$push": "$emp_code"},
                "joining_dates": {"$push": "$joined_on"},
            }
        },
        {"$set": {"offset": {"$range": [0, day_count]}}},
        {"$unwind": "$offset"},
        {
            "$set": {
                "day_instant": {
                    "$dateAdd": {
                        "startDate": start,
                        "unit": "day",
                        "amount": "$offset",
                    }
                }
            }
        },
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "date": "$day_instant",
                        "format": "%Y-%m-%d",
                        "timezone": "Asia/Kolkata",
                    }
                },
                "is_working_day": {
                    "$in": [
                        {
                            "$dayOfWeek": {
                                "date": "$day_instant",
                                "timezone": "Asia/Kolkata",
                            }
                        },
                        [2, 3, 4, 5, 6],
                    ]
                },
            }
        },
        {
            "$set": {
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": "$joining_dates",
                            "as": "joined",
                            "cond": {"$lte": ["$$joined", "$date"]},
                        }
                    }
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "day": "$date",
                    "employee_codes": "$employee_codes",
                },
                "pipeline": daily_department_counts_pipeline(),
                "as": "counts",
            }
        },
        {
            "$set": {
                "counts": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$counts", 0]},
                        {"present_count": 0, "late_count": 0},
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": "$counts.present_count",
                "late_count": "$counts.late_count",
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        mongo_half_up(
                            {"$divide": ["$counts.present_count", "$headcount"]},
                            4,
                        ),
                        None,
                    ]
                },
            }
        },
        {
            "$set": {
                "rate_decimal": {
                    "$convert": {
                        "input": "$attendance_rate",
                        "to": "decimal",
                        "onNull": None,
                    }
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_average": {
                        "$avg": "$rate_decimal",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {
                    "$cond": [
                        {"$ne": ["$moving_average", None]},
                        mongo_half_up("$moving_average", 4),
                        None,
                    ]
                },
            }
        },
        {"$sort": {"date": 1}},
    ]
def attendance_filter(
    emp_code=None,
    date_from=None,
    date_to=None,
    status=None,
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            422,
            "date_from must be on or before date_to",
        )

    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from.isoformat()
        if date_to:
            query["date"]["$lte"] = date_to.isoformat()

    if status:
        query["status"] = status

    return query
def attendance_date(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> str:
    local_time = punch_in.astimezone(IST)
    shift_date = local_time.date()
    clock_time = local_time.strftime("%H:%M")

    if shift_end <= shift_start and clock_time < shift_end:
        shift_date -= timedelta(days=1)

    return shift_date.isoformat()
# --------------------------------------------------------------------------- #
def serialize_times(value):
    if isinstance(value, datetime):
        return to_epoch_ms(value)

    if isinstance(value, dict):
        return {
            key: serialize_times(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [serialize_times(item) for item in value]

    return value
def to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (value - epoch) // timedelta(milliseconds=1)
def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    date_str: str,
) -> int:
    h, m = map(int, shift_start.split(":"))
    start = datetime.fromisoformat(date_str).replace(
        hour=h,
        minute=m,
        tzinfo=IST,
    )

    punch_in = punch_in.replace(microsecond=0)
    seconds_late = (
        punch_in.astimezone(IST) - start
    ).total_seconds()

    if seconds_late <= 600:
        return 0

    return int(seconds_late // 60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int((punch_out - punch_in).total_seconds())
    hours = Decimal(seconds) / Decimal("3600")

    return float(
        hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str,
) -> int:
    h, m = map(int, shift_end.split(":"))

    end = datetime.fromisoformat(date_str).replace(
        hour=h,
        minute=m,
        tzinfo=IST,
    )

    if shift_end <= shift_start:
        end += timedelta(days=1)

    seconds = (punch_out.astimezone(IST) - end).total_seconds()

    if seconds < 1800:
        return 0

    return int(seconds // 60)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    joined_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        date.fromisoformat(value)
        return value
    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: int = Field(
        default_factory=lambda: to_epoch_ms(datetime.now(timezone.utc)),
        strict=True,
        ge=100000000000,
        le=4102444800000,
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"



class PunchOutIn(BaseModel):

    emp_code: str
    punched_at: int = Field(
        default_factory=lambda: to_epoch_ms(datetime.now(timezone.utc)),
        strict=True,
        ge=100000000000,
        le=4102444800000,
    )
class RegularizeIn(BaseModel):
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100000000000,
        le=4102444800000,
    )
    punch_out: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100000000000,
        le=4102444800000,
    )
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable"
        )
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc.pop("_id", None)
    return serialize_times(doc)

@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )
    for employee in items:
        employee["created_at"] = to_epoch_ms(employee.get("created_at"))

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="employee not found",
        )
    ts = datetime.fromtimestamp(
        body.punched_at // 1000,
        tz=timezone.utc,
    )
    d = attendance_date(ts, emp["shift_start"], emp["shift_end"])
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    doc.pop("_id", None)
    return serialize_times(doc)



@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(404, "employee not found")

    ts = datetime.fromtimestamp(
        body.punched_at // 1000,
        tz=timezone.utc,
    )

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(404, "no punch-in found")

    punch_in_time = record["punch_in"]
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    elapsed = ts - punch_in_time
    if elapsed <= timedelta(0) or elapsed > timedelta(hours=24):
        raise HTTPException(
            422,
            "punch-out must be after punch-in and within 24 hours",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")

    work_hours = compute_work_hours(punch_in_time, ts)

    changes = {
        "punch_out": ts,
        "work_hours": work_hours,
        "overtime_minutes": compute_overtime(
            ts,
            emp["shift_start"],
            emp["shift_end"],
            record["date"],
        ),
        "half_day": work_hours < 4.50,
    }

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {"$set": changes},
    )

    if result.modified_count != 1:
        raise HTTPException(409, "already punched out")

    record.update(changes)
    record.pop("_id", None)
    return serialize_times(record)
def mongo_explain(operation):
    result = db.command(
        {
            "explain": operation,
            "verbosity": "executionStats",
        }
    )

    return json.loads(json_util.dumps(result))
@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = attendance_filter(emp_code, date_from, date_to, status)
    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size

    page_docs = list(
        db.attendance_logs.find(q)
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    for d in page_docs:
        d.pop("_id", None)
        d.pop("id", None)
        d.setdefault("half_day", False)
        d.setdefault("history", [])

        d["punch_in"] = to_epoch_ms(d.get("punch_in"))
        d["punch_out"] = to_epoch_ms(d.get("punch_out"))
        d["history"] = serialize_times(d["history"])

    return {
        "items": page_docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: date,
    body: RegularizeIn,
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date.isoformat()}
    )
    if record is None:
        raise HTTPException(404, "attendance record not found")

    fields = body.model_fields_set
    if any(
        field in fields and getattr(body, field) is None
        for field in ("status", "punch_in", "punch_out")
    ):
        raise HTTPException(422, "correction fields cannot be null")

    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    if old_in is not None and old_in.tzinfo is None:
        old_in = old_in.replace(tzinfo=timezone.utc)
    if old_out is not None and old_out.tzinfo is None:
        old_out = old_out.replace(tzinfo=timezone.utc)

    status = body.status if body.status is not None else record["status"]
    punch_in = old_in
    punch_out = old_out

    if "punch_in" in fields:
        punch_in = datetime.fromtimestamp(
            body.punch_in // 1000,
            tz=timezone.utc,
        )
    if "punch_out" in fields:
        punch_out = datetime.fromtimestamp(
            body.punch_out // 1000,
            tz=timezone.utc,
        )

    if status in ("ABSENT", "LEAVE"):
        if "punch_in" in fields or "punch_out" in fields:
            raise HTTPException(
                422,
                "ABSENT or LEAVE cannot include punch times",
            )
        punch_in = None
        punch_out = None
    elif punch_in is None:
        raise HTTPException(422, "a presence status requires punch_in")

    if punch_in is not None:
        expected_date = attendance_date(
            punch_in,
            emp["shift_start"],
            emp["shift_end"],
        )
        if expected_date != date.isoformat():
            raise HTTPException(422, "punch_in must be on the attendance date")

    if punch_out is not None:
        elapsed = punch_out - punch_in
        if elapsed <= timedelta(0) or elapsed > timedelta(hours=24):
            raise HTTPException(
                422,
                "punch_out must be after punch_in and within 24 hours",
            )

    work_hours = (
        compute_work_hours(punch_in, punch_out)
        if punch_in is not None and punch_out is not None
        else None
    )

    final_values = {
        "status": status,
        "punch_in": punch_in,
        "punch_out": punch_out,
        "late_minutes": (
            compute_late_minutes(punch_in, emp["shift_start"], date.isoformat())
            if punch_in is not None and status not in ("ABSENT", "LEAVE")
            else 0
        ),
        "work_hours": work_hours,
        "overtime_minutes": (
            compute_overtime(
                punch_out,
                emp["shift_start"],
                emp["shift_end"],
                date.isoformat(),
            )
            if punch_out is not None
            else 0
        ),
        "half_day": work_hours is not None and work_hours < 4.50,
    }

    audit_changes = {}
    for field, value in final_values.items():
        old_value = record.get(field)
        if field == "punch_in":
            old_value = old_in
        elif field == "punch_out":
            old_value = old_out

        if old_value != value:
            audit_changes[field] = {"from": old_value, "to": value}

    if not audit_changes:
        raise HTTPException(422, "correction does not change the record")

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": audit_changes,
    }

    history_filter = (
        {"history": record["history"]}
        if "history" in record
        else {"history": {"$exists": False}}
    )
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], **history_filter},
        {
            "$set": {
                field: value
                for field, value in final_values.items()
                if field in audit_changes
            },
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(409, "record changed; retry the correction")

    record.update(
        {
            field: value
            for field, value in final_values.items()
            if field in audit_changes
        }
    )
    record.setdefault("history", []).append(history_entry)
    record.pop("_id", None)
    return serialize_times(record)
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
):
    first_day, last_day = month_bounds(month)

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    month_start = datetime(
        first_day.year,
        first_day.month,
        1,
        tzinfo=IST,
    )

    calendar_day = {
        "$dateAdd": {
            "startDate": month_start,
            "unit": "day",
            "amount": "$$offset",
        }
    }

    working_days_pipeline = [
        {"$match": {"emp_code": emp_code}},
        {
            "$project": {
                "_id": 0,
                "working_days": {
                    "$size": {
                        "$filter": {
                            "input": {"$range": [0, last_day.day]},
                            "as": "offset",
                            "cond": {
                                "$and": [
                                    {
                                        "$gte": [
                                            {
                                                "$dateToString": {
                                                    "date": calendar_day,
                                                    "format": "%Y-%m-%d",
                                                    "timezone": "Asia/Kolkata",
                                                }
                                            },
                                            "$joined_on",
                                        ]
                                    },
                                    {
                                        "$in": [
                                            {
                                                "$dayOfWeek": {
                                                    "date": calendar_day,
                                                    "timezone": "Asia/Kolkata",
                                                }
                                            },
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                        }
                    }
                },
            }
        },
    ]

    calendar_result = next(
        db.employees.aggregate(working_days_pipeline)
    )
    working_days = calendar_result["working_days"]

    totals = next(
        db.attendance_logs.aggregate(
            monthly_attendance_pipeline(emp_code, first_day, last_day)
        ),
        None,
    )

    if totals is None:
        totals = {
            "present_days": 0,
            "leave_days": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "total_overtime_minutes": 0,
        }

    attendance_pct = None
    if working_days > 0:
        percentage = (
            Decimal(str(totals["present_days"]))
            / Decimal(working_days)
            * Decimal("100")
        )
        attendance_pct = float(
            percentage.quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        **totals,
        "attendance_pct": attendance_pct,
    }
@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
):
    first_day, last_day = month_bounds(month)

    items = list(
        db.employees.aggregate(
            department_summary_pipeline(
                first_day,
                last_day,
                department,
            )
        )
    )

    return {
        "month": month,
        "items": items,
    }
@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    first_day, last_day = month_bounds(month)

    items = list(
        db.attendance_logs.aggregate(
            late_leaderboard_pipeline(
                first_day,
                last_day,
                limit,
                department,
            )
        )
    )

    return {
        "month": month,
        "items": items,
    }
@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
):
    day_count = (date_to - date_from).days + 1

    if day_count < 1 or day_count > 92:
        raise HTTPException(
            422,
            "date range must contain 1 to 92 days",
        )

    if db.employees.find_one({"department": department}) is None:
        raise HTTPException(404, "department not found")

    items = list(
        db.employees.aggregate(
            department_trend_pipeline(
                department,
                date_from,
                date_to,
            )
        )
    )

    return {
        "department": department,
        "items": items,
    }
@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    ],
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[
        Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
    ] = None,
    trend_from: Optional[date] = Query(default=None, alias="from"),
    trend_to: Optional[date] = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if endpoint == "attendance_list":
        collection = "attendance_logs"
        operation = {
            "find": collection,
            "filter": attendance_filter(
                emp_code,
                date_from,
                date_to,
                status,
            ),
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }

    else:
        if endpoint == "department_trend":
            if department is None or trend_from is None or trend_to is None:
                raise HTTPException(
                    422,
                    "department, from and to are required",
                )

            days = (trend_to - trend_from).days + 1
            if days < 1 or days > 92:
                raise HTTPException(
                    422,
                    "date range must contain 1 to 92 days",
                )

            collection = "employees"
            pipeline = department_trend_pipeline(
                department,
                trend_from,
                trend_to,
            )

        else:
            if month is None:
                raise HTTPException(422, "month is required")

            first_day, last_day = month_bounds(month)

            if endpoint == "employee_monthly":
                if emp_code is None:
                    raise HTTPException(422, "emp_code is required")

                collection = "attendance_logs"
                pipeline = monthly_attendance_pipeline(
                    emp_code,
                    first_day,
                    last_day,
                )

            elif endpoint == "department_summary":
                collection = "employees"
                pipeline = department_summary_pipeline(
                    first_day,
                    last_day,
                    department,
                )

            else:
                collection = "attendance_logs"
                pipeline = late_leaderboard_pipeline(
                    first_day,
                    last_day,
                    limit,
                    department,
                )

        operation = {
            "aggregate": collection,
            "pipeline": pipeline,
            "cursor": {},
        }

    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": mongo_explain(operation),
    }
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

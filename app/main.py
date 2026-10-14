"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from dotenv import load_dotenv
from sklearn import pipeline
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
from pydantic import BaseModel, Field
from datetime import date

from bson import json_util
import json

def make_json_safe(value):
    return json.loads(json_util.dumps(value))


load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

# Create indexes safely on application startup
db.employees.create_index("emp_code", unique=True)
db.employees.create_index([("department", 1), ("joined_on", 1)])

db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1)],
    unique=True
)
db.attendance_logs.create_index([("date", 1), ("emp_code", 1)])
db.attendance_logs.create_index([("date", 1), ("late_minutes", -1)])


IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.get("/")
def home():
    return {
        "message": "Employee Attendance & Analytics API is running",
        "docs": "/docs",
        "health": "/health"
    }



# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)
    local = punch_in.astimezone(IST).replace(microsecond=0)
    h, m = map(int, shift_start.split(":"))
    start = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if local.hour * 60 + local.minute < h * 60 + m:
        start -= timedelta(days=1)
    elapsed = (local - start).total_seconds()
    return int(elapsed // 60) if elapsed > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    from decimal import Decimal, ROUND_HALF_UP
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    return float((seconds / Decimal(3600)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ))


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(
        hour=h, minute=m, second=0, microsecond=0, tzinfo=IST
    )
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)
    punch_out = punch_out.astimezone(IST).replace(microsecond=0)

    if punch_out < end:
        punch_out += timedelta(days=1)

    elapsed = int((punch_out - end).total_seconds() // 60)
    return elapsed if elapsed >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class RegularizeIn(BaseModel):
    status: str | None = None
    punch_in: int | None = None
    punch_out: int | None = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)



# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception as e:
        print("MongoDB connection error:", repr(e))
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents({})
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    if body.punched_at is not None:
        if isinstance(body.punched_at, bool) or not isinstance(body.punched_at, int) or body.punched_at < 100000000000:
            raise HTTPException(422, "punched_at must be epoch milliseconds")
        try:
            ts = datetime.fromtimestamp(body.punched_at / 1000, timezone.utc)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(422, "Invalid punched_at timestamp")
    else:
        ts = datetime.now(timezone.utc)

    ts = ts.replace(microsecond=0, tzinfo=None)
    d = ts.replace(tzinfo=timezone.utc).astimezone(IST).date().isoformat()

    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    db.attendance_logs.insert_one(doc)
    result = dict(doc)
    for key in ("punch_in", "punch_out"):
        if isinstance(result.get(key), datetime):
            result[key] = int(result[key].replace(tzinfo=timezone.utc).timestamp() * 1000)
    return result


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(422, "page must be >= 1 and page_size must be 1-100")
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    total = db.attendance_logs.count_documents(q)
    page_docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort("date", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    for d in page_docs:
        for key in ("punch_in", "punch_out"):
            if isinstance(d.get(key), datetime):
                d[key] = int(d[key].replace(tzinfo=timezone.utc).timestamp() * 1000)
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    if body.punched_at is not None:
        if isinstance(body.punched_at, bool) or not isinstance(body.punched_at, int) or body.punched_at < 100000000000:
            raise HTTPException(422, "punched_at must be epoch milliseconds")
        try:
            ts_utc = datetime.fromtimestamp(
                body.punched_at / 1000, timezone.utc
            ).replace(microsecond=0, tzinfo=None)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(422, "Invalid punched_at timestamp")
    else:
        ts_utc = datetime.now(timezone.utc).replace(
            microsecond=0, tzinfo=None
        )

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts_utc},
        },
        sort=[("punch_in", -1)],
    )

    if not record:
        raise HTTPException(404, "No punch-in record found")

    if record.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")

    punch_in_utc = record["punch_in"].replace(tzinfo=timezone.utc)
    punch_out_utc = ts_utc.replace(tzinfo=timezone.utc)

    if punch_out_utc <= punch_in_utc:
        raise HTTPException(422, "Punch-out must be after punch-in")

    if (punch_out_utc - punch_in_utc).total_seconds() > 86400:
        raise HTTPException(422, "Punch-out must be within 24 hours")

    hours = compute_work_hours(punch_in_utc, punch_out_utc)
    punch_out_ist = punch_out_utc.astimezone(IST)
    overtime = compute_overtime(
        punch_out_ist, emp["shift_end"], record["date"]
    )

    db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts_utc,
            "work_hours": hours,
            "overtime_minutes": overtime if overtime >= 30 else 0,
            "half_day": hours < 4.50,
        }},
    )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}, {"_id": 0}
    )
    for key in ("punch_in", "punch_out"):
        if isinstance(updated.get(key), datetime):
            updated[key] = int(
                updated[key].replace(tzinfo=timezone.utc).timestamp() * 1000
            )
    return updated


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if not record:
        raise HTTPException(404, "Attendance record not found")

    if body.status is not None and body.status not in {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }:
        raise HTTPException(422, "Invalid attendance status")

    if body.status in {"ABSENT", "LEAVE"} and (
        body.punch_in is not None or body.punch_out is not None
    ):
        raise HTTPException(422, "ABSENT/LEAVE cannot have punch times")

    updated_status = body.status or record.get("status", "PRESENT")

    def parse_epoch(value):
        if value is None:
            return None
        if value < 100000000000:
            raise HTTPException(422, "Timestamps must be epoch milliseconds")
        return datetime.fromtimestamp(
            value / 1000, timezone.utc
        ).replace(microsecond=0, tzinfo=None)

    new_in = (
        parse_epoch(body.punch_in)
        if body.punch_in is not None
        else record.get("punch_in")
    )
    new_out = (
        parse_epoch(body.punch_out)
        if body.punch_out is not None
        else record.get("punch_out")
    )

    if updated_status in {"ABSENT", "LEAVE"}:
        new_in, new_out = None, None
    elif new_in is None:
        raise HTTPException(422, "Presence status requires punch_in")

    if new_in is not None:
        local_in = new_in.replace(tzinfo=timezone.utc).astimezone(IST)
        if local_in.date().isoformat() != date:
            raise HTTPException(422, "punch_in must match attendance date")

    if new_out is not None:
        if new_in is None or new_out <= new_in:
            raise HTTPException(422, "punch_out must be after punch_in")
        if (new_out - new_in).total_seconds() > 86400:
            raise HTTPException(422, "Punch interval cannot exceed 24 hours")

    late = (
        compute_late_minutes(new_in, emp["shift_start"])
        if new_in is not None else 0
    )
    hours = (
        compute_work_hours(
            new_in.replace(tzinfo=timezone.utc),
            new_out.replace(tzinfo=timezone.utc)
        )
        if new_in is not None and new_out is not None else 0.0
    )
    overtime = (
        compute_overtime(
            new_out.replace(tzinfo=timezone.utc).astimezone(IST),
            emp["shift_end"], date
        )
        if new_out is not None else 0
    )
    overtime = overtime if overtime >= 30 else 0
    half_day = hours < 4.50 if new_out is not None else False

    old_values = {
        "status": record.get("status"),
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "late_minutes": record.get("late_minutes", 0),
        "work_hours": record.get("work_hours", 0.0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }
    new_values = {
        "status": updated_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "late_minutes": late,
        "work_hours": hours,
        "overtime_minutes": overtime,
        "half_day": half_day,
    }

    changes = {}
    for key, value in new_values.items():
        if old_values[key] != value:
            def output_value(v):
                if isinstance(v, datetime):
                    return int(v.replace(tzinfo=timezone.utc).timestamp() * 1000)
                return v
            changes[key] = {
                "from": output_value(old_values[key]),
                "to": output_value(value),
            }

    if not changes:
        raise HTTPException(422, "Request makes no changes")

    history_entry = {
        "at": int(datetime.now(timezone.utc).timestamp() * 1000),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    db.attendance_logs.update_one(
        {"_id": record["_id"]},
        {
            "$set": new_values,
            "$push": {"history": history_entry},
        },
    )

    result = db.attendance_logs.find_one(
        {"_id": record["_id"]}, {"_id": 0}
    )

    for key in ("punch_in", "punch_out"):
        if isinstance(result.get(key), datetime):
            result[key] = int(
                result[key].replace(tzinfo=timezone.utc).timestamp() * 1000
            )

    return result



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    if not __import__("re").fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(422, "month must be YYYY-MM")

    year, mon = map(int, month.split("-"))
    if mon < 1 or mon > 12:
        raise HTTPException(422, "Invalid month")

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    start = datetime(year, mon, 1)
    end = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
    joined = datetime.fromisoformat(emp["joined_on"])
    working_days = sum(
        1 for day in range(1, (end - start).days + 1)
        if (start + timedelta(days=day - 1)).weekday() < 5
        and (start + timedelta(days=day - 1)).date() >= joined.date()
    )

    records = list(db.attendance_logs.find({
        "emp_code": emp_code,
        "date": {"$gte": month + "-01", "$lt": end.strftime("%Y-%m-%d")}
    }))

    present_days = 0.0
    leave_days = 0
    late_count = 0

    for rec in records:
        day = datetime.fromisoformat(rec["date"]).weekday()
        if rec.get("status") == "LEAVE":
            leave_days += 1
        if day < 5 and rec.get("status") in {"PRESENT", "WFH", "ON_DUTY"}:
            present_days += 0.5 if rec.get("half_day", False) else 1.0
        if rec.get("late_minutes", 0) > 0:
            late_count += 1

    attendance_pct = (
        round(present_days / working_days * 100, 4)
        if working_days else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary():
    pipeline = [
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1}
            }
        },
        {"$sort": {"_id": 1}}
    ]

    departments = list(db.employees.aggregate(pipeline))
    result = []

    for dept in departments:
        department = dept["_id"]
        employees = list(db.employees.find({"department": department}))
        total_present = 0.0
        total_late = 0
        total_overtime = 0

        for emp in employees:
            logs = db.attendance_logs.find({
                "emp_code": emp["emp_code"]
            })
            for log in logs:
                if log.get("status") in {"PRESENT", "WFH", "ON_DUTY"}:
                    total_present += (
                        0.5 if log.get("half_day", False) else 1.0
                    )
                total_late += log.get("late_minutes", 0)
                total_overtime += log.get("overtime_minutes", 0)

        result.append({
            "department": department,
            "headcount": dept["headcount"],
            "present_days": round(total_present, 2),
            "late_minutes": total_late,
            "overtime_minutes": total_overtime
        })

    return {"items": result, "total": len(result)}



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    import re

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "month must be YYYY-MM")

    if limit < 1 or limit > 50:
        raise HTTPException(422, "limit must be between 1 and 50")

    start_date = month + "-01"
    year, mon = map(int, month.split("-"))
    next_month = (
        f"{year + 1}-01" if mon == 12
        else f"{year}-{mon + 1:02d}"
    )

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lt": next_month},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
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
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "emp_code": 1,
            }
        },
    ])

    rows = list(db.attendance_logs.aggregate(pipeline))

    rank = 0
    previous_total = None

    for position, row in enumerate(rows, start=1):
        total = row["total_late_minutes"]
        if total != previous_total:
            rank = position
            previous_total = total
        row["rank"] = rank

    rows = [row for row in rows if row["rank"] <= limit]

    return {"items": rows, "total": len(rows)}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = None, to: str = None):
    from datetime import date as date_type
    from decimal import Decimal, ROUND_HALF_UP

    if not from_date or not to:
        raise HTTPException(422, "from and to dates are required")

    try:
        start_date = date_type.fromisoformat(from_date)
        end_date = date_type.fromisoformat(to)
    except ValueError:
        raise HTTPException(422, "Dates must use YYYY-MM-DD")

    if start_date.isoformat() != from_date or end_date.isoformat() != to:
        raise HTTPException(422, "Dates must use YYYY-MM-DD")

    if end_date < start_date:
        raise HTTPException(422, "to must not be before from")

    days = (end_date - start_date).days + 1
    if days > 92:
        raise HTTPException(422, "Date range cannot exceed 92 days")

    employees = list(db.employees.find({"department": department}))
    if not employees:
        raise HTTPException(404, "Department not found")

    pipeline = [
        {"$match": {"date": {"$gte": from_date, "$lte": to}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$group": {
                "_id": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                    }
                },
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": ["$half_day", 0.5, 1.0]},
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
            }
        },
        {"$sort": {"_id": 1}},
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [
                        datetime.combine(start_date, datetime.min.time()),
                        datetime.combine(end_date, datetime.min.time()),
                    ],
                },
            }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": {"$literal": employees},
                            "as": "emp",
                            "cond": {"$lte": ["$$emp.joined_on", {
                                "$dateToString": {
                                    "date": "$_id",
                                    "format": "%Y-%m-%d",
                                }
                            }]},
                        }
                    }
                },
                "is_working_day": {
                    "$lt": [{"$dayOfWeek": "$_id"}, 7]
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]},
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {"$dateToString": {"date": "$_id", "format": "%Y-%m-%d"}},
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "is_working_day": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
    ]

    daily_rows = list(db.attendance_logs.aggregate(pipeline))
    items = []

    for row in daily_rows:
        moving_avg = row.get("moving_avg_7d")
        items.append({
            "date": row["date"],
            "is_working_day": row["is_working_day"],
            "headcount": row["headcount"],
            "present_count": row["present_count"],
            "late_count": row["late_count"],
            "attendance_rate": (
                float(Decimal(str(row["attendance_rate"])).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                ))
                if row.get("attendance_rate") is not None else None
            ),
            "moving_avg_7d": (
                float(Decimal(str(moving_avg)).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                ))
                if moving_avg is not None else None
            ),
        })

    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to: Optional[str] = None,
):
    if endpoint not in {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }:
        raise HTTPException(422, "Unsupported endpoint")

    if endpoint == "attendance_list":
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if status:
            query["status"] = status
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to

        return db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": query,
                "sort": [("date", -1)],
            },
            "verbosity": "executionStats",
        })

    
    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")

        year, month_num = map(int, month.split("-"))
        next_month = date(
            year + (month_num == 12),
            1 if month_num == 12 else month_num + 1,
            1,
        ).isoformat()

        query = {
            "emp_code": emp_code,
            "date": {"$gte": month + "-01", "$lt": next_month},
        }

        return make_json_safe(db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": query,
            },
            "verbosity": "executionStats",
        }))


    if endpoint == "department_summary":
        return db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": [
                    {"$group": {"_id": "$department", "count": {"$sum": 1}}}
                ],
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")
        return db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": [
                    {"$match": {
                        "date": {"$gte": month + "-01", "$lt": month + "-32"},
                        "late_minutes": {"$gt": 0},
                    }},
                    {"$group": {
                        "_id": "$emp_code",
                        "total_late_minutes": {"$sum": "$late_minutes"},
                    }},
                ],
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

    if endpoint == "department_trend":
        if not department or not from_date or not to:
            raise HTTPException(422, "department, from, and to are required")
        return make_json_safe(db.command(
    "explain",
    {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
    verbosity="executionStats",
))

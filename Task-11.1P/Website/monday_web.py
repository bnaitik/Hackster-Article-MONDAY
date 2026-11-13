"""Monday local-first web API. Shares C:\\MondayServer\\monday.db with the existing AI server."""
from __future__ import annotations
import os, sqlite3, hmac, json
from contextlib import contextmanager
from datetime import datetime, date, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
SERVER_DIR = Path(os.getenv("MONDAY_SERVER_DIR", str(BASE))).expanduser().resolve()
DB_PATH = Path(os.getenv("MONDAY_DB_PATH", str(SERVER_DIR / "monday.db"))).expanduser().resolve()
API_TOKEN = os.getenv("MONDAY_API_TOKEN", "").strip()

app = FastAPI(title="Monday Desk Bot Website API", version="1.0.0")

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

def expire_overdue_sessions() -> list[int]:
    """Mark elapsed sessions complete before returning status to the Pi."""
    current = datetime.now().astimezone()
    expired: list[int] = []
    with connect() as db:
        rows = db.execute(
            "SELECT id, planned_end FROM study_sessions WHERE status='active'"
        ).fetchall()
        for row in rows:
            try:
                deadline = datetime.fromisoformat(row["planned_end"])
                if deadline.tzinfo is None:
                    deadline = deadline.astimezone()
            except (TypeError, ValueError):
                continue
            if deadline <= current:
                db.execute(
                    "UPDATE study_sessions SET status='completed', ended_at=? "
                    "WHERE id=? AND status='active'",
                    (current.isoformat(timespec="seconds"), row["id"]),
                )
                if db.execute("SELECT changes()").fetchone()[0]:
                    expired.append(int(row["id"]))
    return expired

@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def cols(db, table: str) -> set[str]:
    return {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            description TEXT DEFAULT '', due_date TEXT DEFAULT '',
            priority TEXT DEFAULT 'normal', completed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            due_at TEXT NOT NULL, created_at TEXT NOT NULL)""")
        additions = {
            "subject_id": "INTEGER", "start_date": "TEXT DEFAULT ''",
            "progress": "INTEGER DEFAULT 0", "progress_note": "TEXT DEFAULT ''",
            "updated_at": "TEXT DEFAULT ''"
        }
        existing = cols(db, "tasks")
        for name, definition in additions.items():
            if name not in existing:
                db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS semesters (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
          start_date TEXT DEFAULT '', end_date TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS subjects (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          semester_id INTEGER NOT NULL REFERENCES semesters(id) ON DELETE CASCADE,
          code TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(semester_id, code));
        CREATE TABLE IF NOT EXISTS task_progress (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
          progress INTEGER NOT NULL, note TEXT DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS study_sessions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
          started_at TEXT NOT NULL, planned_minutes INTEGER NOT NULL,
          planned_end TEXT NOT NULL, ended_at TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active', notes TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS alarms (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          alarm_time TEXT NOT NULL, alarm_date TEXT DEFAULT '',
          repeat_days TEXT DEFAULT '[]', topic TEXT DEFAULT '',
          enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS alarm_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT, alarm_id INTEGER,
          title TEXT NOT NULL, scheduled_for TEXT NOT NULL,
          triggered_at TEXT DEFAULT '', dismissed_at TEXT DEFAULT '',
          trivia_total INTEGER DEFAULT 0, trivia_correct INTEGER DEFAULT 0,
          outcome TEXT DEFAULT 'scheduled');
        CREATE INDEX IF NOT EXISTS idx_subjects_semester ON subjects(semester_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_subject ON tasks(subject_id);
        CREATE INDEX IF NOT EXISTS idx_progress_task ON task_progress(task_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_task ON study_sessions(task_id, started_at);
        """)
        db.execute("UPDATE tasks SET progress = CASE WHEN completed = 1 THEN 100 ELSE COALESCE(progress, 0) END")
        db.execute("UPDATE tasks SET updated_at = created_at WHERE updated_at IS NULL OR updated_at = ''")

@app.on_event("startup")
def startup():
    init_db()

def require_token(x_api_key: str = Header(default="")):
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="MONDAY_API_TOKEN is not configured in the MondayServer .env file.")
    if not hmac.compare_digest(x_api_key, API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid API key.")

def rowdict(row):
    return dict(row) if row is not None else None

def must_exist(db, table: str, record_id: int, label: str):
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{label} not found.")
    return row

def valid_date(value: Any, label: str, optional=True):
    value = str(value or "").strip()
    if not value and optional:
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label} must use YYYY-MM-DD format.")
    return value

@app.get("/api/ping", dependencies=[Depends(require_token)])
def ping():
    with connect() as db:
        db.execute("SELECT 1")
    return {"status": "connected", "database": str(DB_PATH.name), "time": now_iso()}

@app.get("/api/dashboard", dependencies=[Depends(require_token)])
def dashboard():
    with connect() as db:
        sem_count = db.execute("SELECT COUNT(*) n FROM semesters WHERE status='active'").fetchone()["n"]
        task_stats = db.execute("SELECT COUNT(*) total, SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END) done FROM tasks WHERE subject_id IS NOT NULL").fetchone()
        upcoming = db.execute("""SELECT t.id,t.title,t.due_date,t.priority,s.code subject_code
          FROM tasks t LEFT JOIN subjects s ON s.id=t.subject_id
          WHERE t.subject_id IS NOT NULL AND t.completed=0 AND t.due_date<>'' ORDER BY t.due_date LIMIT 6""").fetchall()
        active = db.execute("""SELECT ss.*,t.title task_title,s.code subject_code
          FROM study_sessions ss JOIN tasks t ON t.id=ss.task_id
          LEFT JOIN subjects s ON s.id=t.subject_id WHERE ss.status='active'
          ORDER BY ss.id DESC LIMIT 1""").fetchone()
        alarms = db.execute("SELECT COUNT(*) n FROM alarms WHERE enabled=1").fetchone()["n"]
    total = task_stats["total"] or 0
    done = task_stats["done"] or 0
    return {"active_semesters": sem_count, "total_tasks": total, "completed_tasks": done,
            "completion_percent": round(done * 100 / total) if total else 0,
            "upcoming_tasks": [rowdict(x) for x in upcoming], "active_session": rowdict(active),
            "enabled_alarms": alarms}

@app.get("/api/semesters", dependencies=[Depends(require_token)])
def list_semesters():
    with connect() as db:
        rows = db.execute("""SELECT se.*,
          COUNT(DISTINCT su.id) subject_count,
          COUNT(DISTINCT t.id) task_count,
          SUM(CASE WHEN t.completed=1 THEN 1 ELSE 0 END) completed_count
          FROM semesters se LEFT JOIN subjects su ON su.semester_id=se.id
          LEFT JOIN tasks t ON t.subject_id=su.id GROUP BY se.id
          ORDER BY CASE se.status WHEN 'active' THEN 0 ELSE 1 END, se.id DESC""").fetchall()
    return [rowdict(r) for r in rows]

@app.post("/api/semesters", dependencies=[Depends(require_token)])
def create_semester(payload: dict):
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Semester name is required.")
    start = valid_date(payload.get("start_date"), "Start date")
    end = valid_date(payload.get("end_date"), "End date")
    with connect() as db:
        cur = db.execute("INSERT INTO semesters(name,start_date,end_date,status,created_at) VALUES(?,?,?,'active',?)", (name,start,end,now_iso()))
        return rowdict(db.execute("SELECT * FROM semesters WHERE id=?", (cur.lastrowid,)).fetchone())

@app.get("/api/semesters/{semester_id}", dependencies=[Depends(require_token)])
def get_semester(semester_id: int):
    with connect() as db:
        semester = rowdict(must_exist(db, "semesters", semester_id, "Semester"))
        semester["subjects"] = [rowdict(r) for r in db.execute("""SELECT su.*,
          COUNT(t.id) task_count, SUM(CASE WHEN t.completed=1 THEN 1 ELSE 0 END) completed_count
          FROM subjects su LEFT JOIN tasks t ON t.subject_id=su.id
          WHERE su.semester_id=? GROUP BY su.id ORDER BY su.code""", (semester_id,)).fetchall()]
        return semester

@app.patch("/api/semesters/{semester_id}", dependencies=[Depends(require_token)])
def update_semester(semester_id: int, payload: dict):
    allowed = {"name", "start_date", "end_date", "status"}
    data = {k:v for k,v in payload.items() if k in allowed}
    if not data: raise HTTPException(400, "No valid fields supplied.")
    if "name" in data and not str(data["name"]).strip(): raise HTTPException(400, "Name cannot be empty.")
    for key in ("start_date", "end_date"):
        if key in data: data[key] = valid_date(data[key], key.replace("_", " ").title())
    if "status" in data and data["status"] not in ("active", "completed", "archived"):
        raise HTTPException(400, "Status must be active, completed or archived.")
    with connect() as db:
        must_exist(db,"semesters",semester_id,"Semester")
        assignments=", ".join(f"{k}=?" for k in data)
        db.execute(f"UPDATE semesters SET {assignments} WHERE id=?", (*data.values(),semester_id))
        return rowdict(must_exist(db,"semesters",semester_id,"Semester"))

@app.post("/api/semesters/{semester_id}/subjects", dependencies=[Depends(require_token)])
def create_subject(semester_id: int, payload: dict):
    code=str(payload.get("code","")).strip().upper(); name=str(payload.get("name","")).strip()
    if not code or not name: raise HTTPException(400,"Subject code and name are required.")
    with connect() as db:
        must_exist(db,"semesters",semester_id,"Semester")
        try:
            cur=db.execute("INSERT INTO subjects(semester_id,code,name,created_at) VALUES(?,?,?,?)",(semester_id,code,name,now_iso()))
        except sqlite3.IntegrityError: raise HTTPException(409,"That subject code already exists in this semester.")
        return rowdict(db.execute("SELECT * FROM subjects WHERE id=?",(cur.lastrowid,)).fetchone())

@app.patch("/api/subjects/{subject_id}", dependencies=[Depends(require_token)])
def update_subject(subject_id: int, payload: dict):
    data={k:v for k,v in payload.items() if k in ("code","name")}
    if not data: raise HTTPException(400,"No valid fields supplied.")
    for k,v in data.items():
        data[k]=str(v).strip().upper() if k=="code" else str(v).strip()
        if not data[k]: raise HTTPException(400,f"{k.title()} cannot be empty.")
    with connect() as db:
        must_exist(db,"subjects",subject_id,"Subject")
        try: db.execute("UPDATE subjects SET "+", ".join(f"{k}=?" for k in data)+" WHERE id=?",(*data.values(),subject_id))
        except sqlite3.IntegrityError: raise HTTPException(409,"That subject code already exists in this semester.")
        return rowdict(must_exist(db,"subjects",subject_id,"Subject"))

@app.get("/api/subjects/{subject_id}", dependencies=[Depends(require_token)])
def get_subject(subject_id: int):
    with connect() as db:
        subject=rowdict(must_exist(db,"subjects",subject_id,"Subject"))
        subject["tasks"]=[rowdict(r) for r in db.execute("SELECT * FROM tasks WHERE subject_id=? ORDER BY CASE WHEN due_date='' THEN 1 ELSE 0 END,due_date,id DESC",(subject_id,)).fetchall()]
        total=len(subject["tasks"]); done=sum(1 for t in subject["tasks"] if t["completed"])
        subject["task_count"]=total; subject["completed_count"]=done
        subject["completion_percent"]=round(done*100/total) if total else 0
        return subject

@app.get("/api/tasks", dependencies=[Depends(require_token)])
def list_all_tasks(status: str = "all", search: str = "", subject_id: int | None = None):
    """Return every task, including tasks created by the voice assistant without a subject."""
    status = str(status or "all").lower().strip()
    if status not in ("all", "open", "completed", "overdue"):
        raise HTTPException(400, "Status must be all, open, completed, or overdue.")

    conditions = []
    params = []
    if status == "open":
        conditions.append("COALESCE(t.completed, 0) = 0")
    elif status == "completed":
        conditions.append("COALESCE(t.completed, 0) = 1")
    elif status == "overdue":
        conditions.append("COALESCE(t.completed, 0) = 0 AND t.due_date <> '' AND t.due_date < ?")
        params.append(date.today().isoformat())
    if subject_id is not None:
        conditions.append("t.subject_id = ?")
        params.append(subject_id)
    search = str(search or "").strip()
    if search:
        conditions.append("(LOWER(t.title) LIKE LOWER(?) OR LOWER(COALESCE(t.description, '')) LIKE LOWER(?) OR LOWER(COALESCE(s.code, '')) LIKE LOWER(?) OR LOWER(COALESCE(s.name, '')) LIKE LOWER(?))")
        term = f"%{search}%"
        params.extend([term, term, term, term])

    where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with connect() as db:
        rows = db.execute(f"""SELECT t.*,
            COALESCE(s.code, '') AS subject_code,
            COALESCE(s.name, '') AS subject_name,
            COALESCE(se.name, '') AS semester_name
          FROM tasks t
          LEFT JOIN subjects s ON s.id = t.subject_id
          LEFT JOIN semesters se ON se.id = s.semester_id
          {where_sql}
          ORDER BY CASE WHEN COALESCE(t.completed, 0)=1 THEN 1 ELSE 0 END,
                   CASE WHEN COALESCE(t.due_date, '')='' THEN 1 ELSE 0 END,
                   t.due_date ASC, t.id DESC""", params).fetchall()
    return [rowdict(r) for r in rows]

@app.post("/api/tasks", dependencies=[Depends(require_token)])
def create_task(payload: dict):
    title = str(payload.get("title", "")).strip()
    if not title:
        raise HTTPException(400, "Task title is required.")

    subject_value = payload.get("subject_id")
    if subject_value in (None, ""):
        raise HTTPException(400, "Choose a subject before creating a task.")
    try:
        subject_id = int(subject_value)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid subject.")

    start = valid_date(payload.get("start_date"), "Start date")
    due = valid_date(payload.get("due_date"), "Due date")
    if start and due and start > due:
        raise HTTPException(400, "Start date cannot be later than the due date.")

    priority = payload.get("priority", "normal")
    if priority not in ("low", "normal", "high"):
        raise HTTPException(400, "Invalid priority.")
    notes = str(payload.get("description", ""))
    stamp = now_iso()

    with connect() as db:
        if subject_id is not None:
            must_exist(db, "subjects", subject_id, "Subject")
        cur = db.execute("""INSERT INTO tasks
            (title,description,due_date,priority,completed,created_at,subject_id,start_date,progress,progress_note,updated_at)
            VALUES(?,?,?,?,0,?,?,?,0,'',?)""",
            (title, notes, due, priority, stamp, subject_id, start, stamp))
        task_id = cur.lastrowid
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_progress'").fetchone():
            db.execute("INSERT INTO task_progress(task_id,progress,note,created_at) VALUES(?,0,'Task created',?)", (task_id, stamp))
        return rowdict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

@app.get("/api/tasks/{task_id}", dependencies=[Depends(require_token)])
def get_task(task_id: int):
    with connect() as db:
        task=rowdict(must_exist(db,"tasks",task_id,"Task"))
        task["history"]=[rowdict(r) for r in db.execute("SELECT * FROM task_progress WHERE task_id=? ORDER BY id DESC",(task_id,)).fetchall()]
        task["sessions"]=[rowdict(r) for r in db.execute("SELECT * FROM study_sessions WHERE task_id=? ORDER BY id DESC",(task_id,)).fetchall()]
        if task["subject_id"]:
            task["subject"]=rowdict(db.execute("SELECT * FROM subjects WHERE id=?",(task["subject_id"],)).fetchone())
        return task

@app.patch("/api/tasks/{task_id}", dependencies=[Depends(require_token)])
def update_task(task_id: int, payload: dict):
    allowed = {"title", "description", "start_date", "due_date", "priority", "progress", "progress_note"}
    data = {k: v for k, v in payload.items() if k in allowed}
    if not data:
        raise HTTPException(400, "No valid task fields were supplied.")

    if "title" in data:
        data["title"] = str(data["title"]).strip()
        if not data["title"]:
            raise HTTPException(400, "Task title cannot be empty.")
    for key in ("start_date", "due_date"):
        if key in data:
            data[key] = valid_date(data[key], key.replace("_", " ").title())
    if "priority" in data and data["priority"] not in ("low", "normal", "high"):
        raise HTTPException(400, "Priority must be low, normal, or high.")
    if "progress" in data:
        try:
            data["progress"] = int(data["progress"])
        except (TypeError, ValueError):
            raise HTTPException(400, "Progress must be between 0 and 100.")
        if not 0 <= data["progress"] <= 100:
            raise HTTPException(400, "Progress must be between 0 and 100.")
    if "description" in data:
        data["description"] = str(data["description"])
    if "progress_note" in data:
        data["progress_note"] = str(data["progress_note"])

    with connect() as db:
        old = must_exist(db, "tasks", task_id, "Task")
        next_start = data.get("start_date", old["start_date"] or "")
        next_due = data.get("due_date", old["due_date"] or "")
        if next_start and next_due and next_start > next_due:
            raise HTTPException(400, "Start date cannot be later than the due date.")

        if "progress" in data:
            data["completed"] = 1 if data["progress"] >= 100 else 0
        data["updated_at"] = now_iso()
        assignments = ", ".join(f"{key}=?" for key in data)
        db.execute(f"UPDATE tasks SET {assignments} WHERE id=?", (*data.values(), task_id))

        if "progress" in data or "progress_note" in data:
            progress_value = data.get("progress", old["progress"] or 0)
            note = data.get("progress_note", old["progress_note"] or "")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_progress'").fetchone():
                db.execute("INSERT INTO task_progress(task_id,progress,note,created_at) VALUES(?,?,?,?)",
                           (task_id, progress_value, note, now_iso()))
        return rowdict(must_exist(db, "tasks", task_id, "Task"))

@app.post("/api/tasks/{task_id}/progress", dependencies=[Depends(require_token)])
def add_progress(task_id: int, payload: dict):
    try: progress=int(payload.get("progress"))
    except (TypeError,ValueError): raise HTTPException(400,"Progress must be between 0 and 100.")
    if not 0<=progress<=100: raise HTTPException(400,"Progress must be between 0 and 100.")
    note=str(payload.get("note", "")).strip(); stamp=now_iso()
    with connect() as db:
        must_exist(db,"tasks",task_id,"Task")
        db.execute("UPDATE tasks SET progress=?,progress_note=?,completed=?,updated_at=? WHERE id=?",(progress,note,1 if progress==100 else 0,stamp,task_id))
        db.execute("INSERT INTO task_progress(task_id,progress,note,created_at) VALUES(?,?,?,?)",(task_id,progress,note,stamp))
        return {"success":True,"task":rowdict(must_exist(db,"tasks",task_id,"Task"))}

@app.delete("/api/tasks/{task_id}", dependencies=[Depends(require_token)])
def delete_task(task_id: int):
    with connect() as db:
        must_exist(db,"tasks",task_id,"Task")
        db.execute("DELETE FROM task_progress WHERE task_id=?", (task_id,))
        db.execute("DELETE FROM study_sessions WHERE task_id=?", (task_id,))
        db.execute("DELETE FROM tasks WHERE id=?",(task_id,))
    return {"success": True, "task_id": task_id, "message": "Task deleted."}

@app.post("/api/tasks/{task_id}/sessions", dependencies=[Depends(require_token)])
def start_session(task_id: int, payload: dict):
    try: minutes=int(payload.get("planned_minutes"))
    except (TypeError,ValueError): raise HTTPException(400,"Enter a session duration in minutes.")
    if not 1<=minutes<=720: raise HTTPException(400,"Session duration must be 1–720 minutes.")
    started=datetime.now().astimezone(); ends=started.timestamp()+minutes*60
    planned_end=datetime.fromtimestamp(ends).astimezone().isoformat(timespec="seconds")
    with connect() as db:
        must_exist(db,"tasks",task_id,"Task")
        active=db.execute("SELECT id FROM study_sessions WHERE status='active' LIMIT 1").fetchone()
        if active: raise HTTPException(409,"A study session is already active. End it before starting another.")
        cur=db.execute("INSERT INTO study_sessions(task_id,started_at,planned_minutes,planned_end,status) VALUES(?,?,?,?,'active')",(task_id,started.isoformat(timespec="seconds"),minutes,planned_end))
        return rowdict(db.execute("SELECT * FROM study_sessions WHERE id=?",(cur.lastrowid,)).fetchone())

@app.post("/api/sessions/{session_id}/end", dependencies=[Depends(require_token)])
def end_session(session_id: int, payload: dict = {}):
    with connect() as db:
        session=must_exist(db,"study_sessions",session_id,"Study session")
        if session["status"]!="active": raise HTTPException(409,"This session has already ended.")
        ended=now_iso(); status="ended_early" if payload.get("ended_early") else "completed"
        db.execute("UPDATE study_sessions SET ended_at=?,status=?,notes=? WHERE id=?",(ended,status,str(payload.get("notes","")),session_id))
        return rowdict(must_exist(db,"study_sessions",session_id,"Study session"))

@app.get("/api/alarms", dependencies=[Depends(require_token)])
def list_alarms():
    with connect() as db:
        return [{**rowdict(r),"repeat_days":json.loads(r["repeat_days"] or "[]")} for r in db.execute("SELECT * FROM alarms ORDER BY enabled DESC,alarm_time").fetchall()]

@app.post("/api/alarms", dependencies=[Depends(require_token)])
def create_alarm(payload: dict):
    title=str(payload.get("title","Morning alarm")).strip() or "Morning alarm"
    alarm_time=str(payload.get("alarm_time","")).strip()
    try: datetime.strptime(alarm_time,"%H:%M")
    except ValueError: raise HTTPException(400,"Alarm time must use 24-hour HH:MM format.")
    alarm_date=valid_date(payload.get("alarm_date"),"Alarm date")
    days=payload.get("repeat_days",[])
    if not isinstance(days,list) or any(d not in ("Mon","Tue","Wed","Thu","Fri","Sat","Sun") for d in days): raise HTTPException(400,"Invalid repeat days.")
    if not alarm_date and not days:
        local_now = datetime.now().astimezone()
        candidate = datetime.combine(local_now.date(), datetime.strptime(alarm_time, "%H:%M").time(), tzinfo=local_now.tzinfo)
        alarm_date = (local_now.date() if candidate > local_now else local_now.date() + timedelta(days=1)).isoformat()
    stamp=now_iso()
    with connect() as db:
        cur=db.execute("INSERT INTO alarms(title,alarm_time,alarm_date,repeat_days,topic,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(title,alarm_time,alarm_date,json.dumps(days),str(payload.get("topic","")),1 if payload.get("enabled",True) else 0,stamp,stamp))
        r=rowdict(db.execute("SELECT * FROM alarms WHERE id=?",(cur.lastrowid,)).fetchone()); r["repeat_days"]=days; return r

@app.patch("/api/alarms/{alarm_id}", dependencies=[Depends(require_token)])
def update_alarm(alarm_id: int, payload: dict):
    allowed={"title","alarm_time","alarm_date","repeat_days","topic","enabled"}
    data={k:v for k,v in payload.items() if k in allowed}
    if not data: raise HTTPException(400,"No valid fields supplied.")
    if "alarm_time" in data:
        try: datetime.strptime(str(data["alarm_time"]),"%H:%M")
        except ValueError: raise HTTPException(400,"Alarm time must use HH:MM format.")
    if "alarm_date" in data: data["alarm_date"]=valid_date(data["alarm_date"],"Alarm date")
    if "repeat_days" in data:
        days=data["repeat_days"]
        if not isinstance(days,list) or any(d not in ("Mon","Tue","Wed","Thu","Fri","Sat","Sun") for d in days): raise HTTPException(400,"Invalid repeat days.")
        data["repeat_days"]=json.dumps(days)
    if "enabled" in data: data["enabled"]=1 if data["enabled"] else 0
    if "title" in data: data["title"]=str(data["title"]).strip()
    if "topic" in data: data["topic"]=str(data["topic"])
    data["updated_at"]=now_iso()
    with connect() as db:
        existing = must_exist(db,"alarms",alarm_id,"Alarm")
        schedule_changed = any(k in data for k in ("alarm_time", "alarm_date", "repeat_days"))
        final_time = str(data.get("alarm_time", existing["alarm_time"]))
        final_date = str(data.get("alarm_date", existing["alarm_date"] or "")).strip()
        final_days_raw = data.get("repeat_days", existing["repeat_days"] or "[]")
        if isinstance(final_days_raw, str):
            try: final_days = json.loads(final_days_raw or "[]")
            except ValueError: final_days = []
        else:
            final_days = final_days_raw
        reenabled_expired_one_time = (
            data.get("enabled") == 1 and not final_days and
            (not final_date or final_date <= datetime.now().astimezone().date().isoformat())
        )
        if not final_date and not final_days and (schedule_changed or reenabled_expired_one_time):
            local_now = datetime.now().astimezone()
            candidate = datetime.combine(local_now.date(), datetime.strptime(final_time, "%H:%M").time(), tzinfo=local_now.tzinfo)
            data["alarm_date"] = (local_now.date() if candidate > local_now else local_now.date() + timedelta(days=1)).isoformat()
        elif reenabled_expired_one_time:
            local_now = datetime.now().astimezone()
            candidate = datetime.combine(local_now.date(), datetime.strptime(final_time, "%H:%M").time(), tzinfo=local_now.tzinfo)
            data["alarm_date"] = (local_now.date() if candidate > local_now else local_now.date() + timedelta(days=1)).isoformat()
        db.execute("UPDATE alarms SET "+", ".join(f"{k}=?" for k in data)+" WHERE id=?",(*data.values(),alarm_id))
        if data.get("enabled") == 0:
            db.execute("UPDATE alarm_history SET outcome='dismissed',dismissed_at=? WHERE alarm_id=? AND outcome='triggered'",(now_iso(),alarm_id))
        r=rowdict(must_exist(db,"alarms",alarm_id,"Alarm")); r["repeat_days"]=json.loads(r["repeat_days"] or "[]"); return r

@app.delete("/api/alarms/{alarm_id}", dependencies=[Depends(require_token)])
def delete_alarm(alarm_id: int):
    with connect() as db:
        must_exist(db,"alarms",alarm_id,"Alarm")
        db.execute("UPDATE alarm_history SET outcome='dismissed',dismissed_at=?,alarm_id=NULL WHERE alarm_id=? AND outcome='triggered'",(now_iso(),alarm_id))
        db.execute("UPDATE alarm_history SET alarm_id=NULL WHERE alarm_id=?",(alarm_id,))
        db.execute("DELETE FROM alarms WHERE id=?",(alarm_id,))
    return {"success":True}

@app.get("/api/alarms/history", dependencies=[Depends(require_token)])
def alarm_history():
    with connect() as db:
        return [rowdict(r) for r in db.execute("SELECT * FROM alarm_history ORDER BY id DESC LIMIT 100").fetchall()]

@app.post("/api/device/poll", dependencies=[Depends(require_token)])
def device_poll():
    """Poll endpoint used by the Pi for study check-ins and actual alarm delivery.

    A matching alarm is claimed once per scheduled local occurrence by inserting
    an alarm_history row in the same transaction. One-time alarms are disabled
    as soon as they are claimed; repeating alarms are deduplicated by history.
    """
    expire_overdue_sessions()
    local_now = datetime.now().astimezone()
    today = local_now.date()
    weekday = local_now.strftime("%a")
    due_alarms = []
    with connect() as db:
        active = db.execute("""SELECT ss.id, ss.started_at, ss.planned_minutes,
                ss.planned_end, t.title task_title, s.code subject_code
            FROM study_sessions ss JOIN tasks t ON t.id=ss.task_id
            LEFT JOIN subjects s ON s.id=t.subject_id
            WHERE ss.status='active' ORDER BY ss.id DESC LIMIT 1""").fetchone()
        alarms = db.execute("SELECT * FROM alarms WHERE enabled=1 ORDER BY alarm_time,id").fetchall()
        for alarm in alarms:
            try:
                days = json.loads(alarm["repeat_days"] or "[]")
            except (TypeError, ValueError):
                days = []
            alarm_date = str(alarm["alarm_date"] or "").strip()
            if alarm_date:
                try:
                    first_date = date.fromisoformat(alarm_date)
                except ValueError:
                    continue
                if today < first_date:
                    continue
            else:
                first_date = None
            if days:
                if weekday not in days:
                    continue
            elif first_date != today:
                continue
            try:
                alarm_clock = datetime.strptime(alarm["alarm_time"], "%H:%M").time()
            except ValueError:
                continue
            scheduled = datetime.combine(today, alarm_clock, tzinfo=local_now.tzinfo)
            age_seconds = (local_now - scheduled).total_seconds()
            if age_seconds < 0 or age_seconds > 900:
                continue
            scheduled_key = scheduled.isoformat(timespec="seconds")
            claimed = db.execute(
                "SELECT id FROM alarm_history WHERE alarm_id=? AND scheduled_for=? AND outcome IN ('triggered','dismissed') LIMIT 1",
                (alarm["id"], scheduled_key)
            ).fetchone()
            if claimed:
                continue
            cursor = db.execute(
                "INSERT INTO alarm_history(alarm_id,title,scheduled_for,triggered_at,outcome) VALUES(?,?,?,?, 'triggered')",
                (alarm["id"], alarm["title"], scheduled_key, local_now.isoformat(timespec="seconds"))
            )
            if not days:
                db.execute("UPDATE alarms SET enabled=0,updated_at=? WHERE id=?",
                           (local_now.isoformat(timespec="seconds"), alarm["id"]))
            due_alarms.append({
                "history_id": cursor.lastrowid,
                "alarm_id": alarm["id"],
                "title": alarm["title"],
                "alarm_time": alarm["alarm_time"],
                "topic": alarm["topic"] or "",
                "scheduled_for": scheduled_key,
                "repeat_days": days
            })

        pending = db.execute("""SELECT ah.id history_id, ah.alarm_id, ah.title,
                ah.scheduled_for, COALESCE(a.alarm_time, substr(ah.scheduled_for, 12, 5)) alarm_time,
                COALESCE(a.topic, '') topic, COALESCE(a.repeat_days, '[]') repeat_days
            FROM alarm_history ah LEFT JOIN alarms a ON a.id=ah.alarm_id
            WHERE ah.outcome='triggered' AND COALESCE(ah.dismissed_at,'')=''
            ORDER BY ah.id""").fetchall()
        known_ids = {int(x['history_id']) for x in due_alarms}
        for item in pending:
            if int(item['history_id']) in known_ids:
                continue
            try:
                repeat_days = json.loads(item['repeat_days'] or '[]')
            except (TypeError, ValueError):
                repeat_days = []
            due_alarms.append({
                "history_id": item['history_id'],
                "alarm_id": item['alarm_id'],
                "title": item['title'],
                "alarm_time": item['alarm_time'],
                "topic": item['topic'],
                "scheduled_for": item['scheduled_for'],
                "repeat_days": repeat_days
            })
    return {
        "server_time": local_now.isoformat(timespec="seconds"),
        "active_session": rowdict(active),
        "due_alarms": due_alarms
    }

@app.post("/api/device/alarms/{history_id}/dismiss", dependencies=[Depends(require_token)])
def dismiss_device_alarm(history_id: int):
    with connect() as db:
        row = db.execute("SELECT * FROM alarm_history WHERE id=?", (history_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Alarm event not found.")
        if row["outcome"] == "dismissed":
            return {"success": True, "already_dismissed": True, "history_id": history_id}
        db.execute("UPDATE alarm_history SET outcome='dismissed',dismissed_at=? WHERE id=?",
                   (now_iso(), history_id))
    return {"success": True, "history_id": history_id, "message": "Alarm dismissed."}

@app.get("/api/reports/semesters/{semester_id}", dependencies=[Depends(require_token)])
def semester_report(semester_id: int):
    with connect() as db:
        sem=rowdict(must_exist(db,"semesters",semester_id,"Semester"))
        subjects=db.execute("""SELECT su.id,su.code,su.name,COUNT(t.id) total,
          SUM(CASE WHEN t.completed=1 THEN 1 ELSE 0 END) completed,
          ROUND(AVG(COALESCE(t.progress,0))) avg_progress
          FROM subjects su LEFT JOIN tasks t ON t.subject_id=su.id
          WHERE su.semester_id=? GROUP BY su.id ORDER BY su.code""",(semester_id,)).fetchall()
        sessions=db.execute("""SELECT COUNT(*) count,COALESCE(SUM((julianday(ended_at)-julianday(started_at))*1440),0) minutes
          FROM study_sessions ss JOIN tasks t ON t.id=ss.task_id JOIN subjects su ON su.id=t.subject_id
          WHERE su.semester_id=? AND ss.ended_at<>''""",(semester_id,)).fetchone()
    return {"semester":sem,"subjects":[rowdict(s) for s in subjects],"session_count":sessions["count"],"study_minutes":round(sessions["minutes"] or 0)}

@app.get("/", include_in_schema=False)
def home():
    return FileResponse(BASE / "static" / "index.html")

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
SERVER_DIR = Path(os.getenv("MONDAY_SERVER_DIR", str(BASE))).expanduser().resolve()
DB_PATH = Path(os.getenv("MONDAY_DB_PATH", str(SERVER_DIR / "monday.db"))).expanduser().resolve()

def get_connection():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db

def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None

def _columns(db, table):
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}

def _date_value(value, label, optional=True):
    value = str(value or "").strip()
    if not value and optional:
        return ""
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} must use YYYY-MM-DD format.")
    return parsed.isoformat()

def initialize_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            due_date TEXT DEFAULT '',
            priority TEXT DEFAULT 'normal',
            completed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            due_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS semesters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_date TEXT DEFAULT '',
            end_date TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semester_id INTEGER NOT NULL REFERENCES semesters(id) ON DELETE CASCADE,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(semester_id, code)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS task_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            progress INTEGER NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            planned_minutes INTEGER NOT NULL,
            planned_end TEXT NOT NULL,
            ended_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            notes TEXT DEFAULT ''
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS alarms (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            alarm_time TEXT NOT NULL, alarm_date TEXT DEFAULT '',
            repeat_days TEXT DEFAULT '[]', topic TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS alarm_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, alarm_id INTEGER,
            title TEXT NOT NULL, scheduled_for TEXT NOT NULL,
            triggered_at TEXT DEFAULT '', dismissed_at TEXT DEFAULT '',
            trivia_total INTEGER DEFAULT 0, trivia_correct INTEGER DEFAULT 0,
            outcome TEXT DEFAULT 'scheduled'
        )""")

        additions = {
            "subject_id": "INTEGER",
            "start_date": "TEXT DEFAULT ''",
            "progress": "INTEGER DEFAULT 0",
            "progress_note": "TEXT DEFAULT ''",
            "updated_at": "TEXT DEFAULT ''"
        }
        existing = _columns(db, "tasks")
        for name, definition in additions.items():
            if name not in existing:
                db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
        db.execute("UPDATE tasks SET updated_at=created_at WHERE updated_at IS NULL OR updated_at=''")

def get_current_time():
    now = datetime.now().astimezone()
    return {
        "current_datetime": now.isoformat(timespec="seconds"),
        "timezone": str(now.tzinfo)
    }

def create_task(title, description="", start_date="", due_date="", priority="normal", subject_code=""):
    """Compatibility wrapper; every academic task must belong to a subject."""
    subject_code = str(subject_code or "").strip()
    if not subject_code:
        return {"success": False, "error": "Please specify the subject code (for example SIT112) so the task is saved inside that subject."}
    return create_academic_task(subject_code, title, start_date, due_date, description, priority)

def list_tasks(include_completed=False):
    with get_connection() as db:
        if include_completed:
            rows = db.execute("""SELECT t.*, COALESCE(s.code, '') AS subject_code
                FROM tasks t JOIN subjects s ON s.id=t.subject_id
                ORDER BY t.completed, CASE WHEN t.due_date='' THEN 1 ELSE 0 END,
                         t.due_date, t.id DESC""").fetchall()
        else:
            rows = db.execute("""SELECT t.*, COALESCE(s.code, '') AS subject_code
                FROM tasks t JOIN subjects s ON s.id=t.subject_id
                WHERE t.completed = 0
                ORDER BY CASE WHEN t.due_date='' THEN 1 ELSE 0 END,
                         t.due_date, t.id DESC""").fetchall()
    return {"tasks": [dict(row) for row in rows], "count": len(rows)}

def complete_task(task_id):
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return {"success": False, "error": "A valid task ID is required."}
    with get_connection() as db:
        row = db.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return {"success": False, "error": f"No task found with ID {task_id}."}
        columns = _columns(db, "tasks")
        updates = ["completed=1"]
        if "progress" in columns:
            updates.append("progress=100")
        if "updated_at" in columns:
            updates.append("updated_at=?")
            db.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id=?",
                       (datetime.now().astimezone().isoformat(timespec="seconds"), task_id))
        else:
            db.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id=?", (task_id,))
    return {"success": True, "task_id": task_id, "message": "Task marked as completed."}

def update_task(task_id, title=None, description=None, start_date=None, due_date=None, priority=None):
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return {"success": False, "error": "A valid task ID is required."}

    changes = {}
    if title is not None:
        title = str(title).strip()
        if not title:
            return {"success": False, "error": "Task title cannot be empty."}
        changes["title"] = title
    if description is not None:
        changes["description"] = str(description)
    try:
        if start_date is not None:
            changes["start_date"] = _date_value(start_date, "Start date")
        if due_date is not None:
            changes["due_date"] = _date_value(due_date, "Due date")
    except ValueError as error:
        return {"success": False, "error": str(error)}
    if priority is not None:
        if priority not in ("low", "normal", "high"):
            return {"success": False, "error": "Priority must be low, normal, or high."}
        changes["priority"] = priority
    if not changes:
        return {"success": False, "error": "Provide at least one field to update."}

    with get_connection() as db:
        old = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if old is None:
            return {"success": False, "error": f"No task found with ID {task_id}."}
        next_start = changes.get("start_date", old["start_date"] if "start_date" in old.keys() else "") or ""
        next_due = changes.get("due_date", old["due_date"] or "") or ""
        if next_start and next_due and next_start > next_due:
            return {"success": False, "error": "Start date cannot be later than the due date."}
        columns = _columns(db, "tasks")
        changes = {k: v for k, v in changes.items() if k in columns}
        if "updated_at" in columns:
            changes["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        assignments = ", ".join(f"{key}=?" for key in changes)
        db.execute(f"UPDATE tasks SET {assignments} WHERE id=?", (*changes.values(), task_id))
        updated = dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
    return {"success": True, "task": updated, "task_id": task_id, "message": "Task updated successfully."}

def delete_task(task_id):
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return {"success": False, "error": "A valid task ID is required."}
    with get_connection() as db:
        row = db.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return {"success": False, "error": f"No task found with ID {task_id}."}
        for child_table in ("task_progress", "study_sessions"):
            if _table_exists(db, child_table):
                db.execute(f"DELETE FROM {child_table} WHERE task_id=?", (task_id,))
        db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return {"success": True, "task_id": task_id, "message": "Task deleted successfully."}

def create_reminder(title, due_at):
    title = str(title or "").strip()
    due_at = str(due_at or "").strip()
    if not title or not due_at:
        return {"success": False, "error": "A reminder title and date/time are required."}
    with get_connection() as db:
        cursor = db.execute("""INSERT INTO reminders(title, due_at, created_at)
            VALUES (?, ?, ?)""",
            (title, due_at, datetime.now().astimezone().isoformat(timespec="seconds")))
        reminder_id = cursor.lastrowid
    return {"success": True, "reminder_id": reminder_id, "title": title,
            "due_at": due_at, "message": "Reminder saved successfully."}

def list_reminders():
    with get_connection() as db:
        rows = db.execute("SELECT * FROM reminders ORDER BY due_at ASC").fetchall()
    return {"reminders": [dict(row) for row in rows], "count": len(rows)}

def hardware_action(action, parameters=None):
    return {"success": True, "queued": True,
            "device_action": {"action": action, "parameters": parameters or {}},
            "message": "Action sent to the Smart Desk Bot for execution."}

def set_light_brightness(percentage):
    if type(percentage) is not int or not 0 <= percentage <= 100:
        return {"success": False, "error": "Brightness must be 0-100."}
    return hardware_action("set_brightness", {"percentage": percentage})

def adjust_light_brightness(adjustment):
    if type(adjustment) is not int or not -100 <= adjustment <= 100:
        return {"success": False, "error": "Adjustment must be -100 to 100."}
    if adjustment == 0:
        return {"success": False, "error": "Adjustment cannot be zero."}
    return hardware_action("adjust_brightness", {"adjustment": adjustment})

def turn_light_on():
    return hardware_action("turn_on")

def turn_light_off():
    return hardware_action("turn_off")

def set_light_auto():
    return hardware_action("auto")

def get_hardware_status():
    return hardware_action("get_status")

TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "get_current_time",
     "description": "Get the actual current local date, time and timezone.",
     "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "create_task",
     "description": "Create a task only inside a specified academic subject. Prefer create_academic_task. Do not create unassigned tasks. Dates must be YYYY-MM-DD.",
     "parameters": {"type": "object", "properties": {
         "title": {"type": "string"}, "description": {"type": "string"},
         "start_date": {"type": "string", "description": "Optional start date in YYYY-MM-DD format."},
         "due_date": {"type": "string", "description": "Optional due date in YYYY-MM-DD format."},
         "priority": {"type": "string", "enum": ["low", "normal", "high"]},
         "subject_code": {"type": "string", "description": "Required subject code such as SIT112."}},
         "required": ["title", "subject_code"]}}},
    {"type": "function", "function": {"name": "list_tasks",
     "description": "Retrieve saved tasks and their exact IDs. Use this before updating or deleting a task. Set include_completed true when searching for completed tasks.",
     "parameters": {"type": "object", "properties": {"include_completed": {"type": "boolean"}}, "required": []}}},
    {"type": "function", "function": {"name": "update_task",
     "description": "Update an existing task by its exact task ID. Only supplied fields change. Use list_tasks first to identify the correct task. Dates must be YYYY-MM-DD; use an empty date string to clear a date.",
     "parameters": {"type": "object", "properties": {
         "task_id": {"type": "integer"}, "title": {"type": "string"},
         "description": {"type": "string"}, "start_date": {"type": "string"},
         "due_date": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high"]}},
         "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "complete_task",
     "description": "Mark a saved task as completed using its exact task ID.",
     "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "delete_task",
     "description": "Permanently delete a task by its exact task ID. Only use after the user explicitly asks to delete it and list_tasks has identified the exact matching task. If more than one task matches, ask which one. This also removes its progress and study-session history.",
     "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "create_reminder",
     "description": "Save a reminder with the date and time supplied or clarified by the user. Never invent a date or time.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "due_at": {"type": "string"}}, "required": ["title", "due_at"]}}},
    {"type": "function", "function": {"name": "list_reminders",
     "description": "Retrieve all saved reminders.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "set_light_brightness",
     "description": "Set the desk light to an exact percentage from 0 to 100.",
     "parameters": {"type": "object", "properties": {"percentage": {"type": "integer"}}, "required": ["percentage"]}}},
    {"type": "function", "function": {"name": "adjust_light_brightness",
     "description": "Increase or decrease desk light brightness by percentage points. Positive means brighter; negative means dimmer.",
     "parameters": {"type": "object", "properties": {"adjustment": {"type": "integer"}}, "required": ["adjustment"]}}},
    {"type": "function", "function": {"name": "turn_light_on", "description": "Turn the desk light on.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "turn_light_off", "description": "Turn the desk light off.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "set_light_auto", "description": "Enable automatic light brightness controlled by the light sensor.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_hardware_status", "description": "Get current desk light, brightness and ambient light sensor status from the physical device.", "parameters": {"type": "object", "properties": {}, "required": []}}}
]

TOOL_FUNCTIONS = {
    "get_current_time": get_current_time,
    "create_task": create_task,
    "list_tasks": list_tasks,
    "update_task": update_task,
    "complete_task": complete_task,
    "delete_task": delete_task,
    "create_reminder": create_reminder,
    "list_reminders": list_reminders,
    "set_light_brightness": set_light_brightness,
    "adjust_light_brightness": adjust_light_brightness,
    "turn_light_on": turn_light_on,
    "turn_light_off": turn_light_off,
    "set_light_auto": set_light_auto,
    "get_hardware_status": get_hardware_status
}

from academic_voice import (
    list_academic_structure, list_academic_tasks, create_academic_task,
    update_academic_task, delete_academic_task, get_academic_task_progress,
    start_academic_session, get_active_academic_session,
    end_active_academic_session, get_remaining_study_time,
    create_alarm, list_alarms, update_alarm, delete_alarm,
)

TOOL_FUNCTIONS.update({
    "list_academic_structure": list_academic_structure,
    "list_academic_tasks": list_academic_tasks,
    "create_academic_task": create_academic_task,
    "update_academic_task": update_academic_task,
    "delete_academic_task": delete_academic_task,
    "get_academic_task_progress": get_academic_task_progress,
    "start_academic_session": start_academic_session,
    "get_active_academic_session": get_active_academic_session,
    "end_active_academic_session": end_active_academic_session,
    "get_remaining_study_time": get_remaining_study_time,
    "create_alarm": create_alarm,
    "list_alarms": list_alarms,
    "update_alarm": update_alarm,
    "delete_alarm": delete_alarm,
})

TOOL_DEFINITIONS.extend([
    {"type":"function","function":{"name":"list_academic_structure","description":"List active semesters and subject codes. Use this to identify the user's subject before creating or changing a task.","parameters":{"type":"object","properties":{"include_archived":{"type":"boolean"}},"required":[]}}},
    {"type":"function","function":{"name":"list_academic_tasks","description":"List all tasks, titles and exact IDs within one subject. Always call this before updating or deleting a task.","parameters":{"type":"object","properties":{"subject_code":{"type":"string"}},"required":["subject_code"]}}},
    {"type":"function","function":{"name":"create_academic_task","description":"Create and save a task inside the specified subject. Dates must be YYYY-MM-DD. Only use dates the user stated; if the subject is missing, ask which subject.","parameters":{"type":"object","properties":{"subject_code":{"type":"string"},"title":{"type":"string"},"start_date":{"type":"string"},"due_date":{"type":"string"},"notes":{"type":"string"},"priority":{"type":"string","enum":["low","normal","high"]}},"required":["subject_code","title"]}}},
    {"type":"function","function":{"name":"update_academic_task","description":"Update an existing task inside a subject. Call list_academic_tasks first, use the exact task title, and supply ONLY the fields the user asked to change. For a due-date request, set due_date to the requested YYYY-MM-DD date. Never substitute today's date or call get_current_time for a user-specified task date.","parameters":{"type":"object","properties":{"subject_code":{"type":"string"},"task_title":{"type":"string"},"start_date":{"type":"string"},"due_date":{"type":"string"},"progress":{"type":"integer"},"progress_note":{"type":"string"},"notes":{"type":"string"},"priority":{"type":"string","enum":["low","normal","high"]}},"required":["subject_code","task_title"]}}},
    {"type":"function","function":{"name":"delete_academic_task","description":"Permanently delete one task from a subject. Only after the user explicitly asks for deletion, call list_academic_tasks and match the exact task title. Ask if multiple tasks match.","parameters":{"type":"object","properties":{"subject_code":{"type":"string"},"task_title":{"type":"string"}},"required":["subject_code","task_title"]}}},
    {"type":"function","function":{"name":"get_academic_task_progress","description":"Get the current progress and due date of a named task in a subject.","parameters":{"type":"object","properties":{"subject_code":{"type":"string"},"task_title":{"type":"string"}},"required":["subject_code","task_title"]}}},
    {"type":"function","function":{"name":"start_academic_session","description":"Start a timed study session for a named task. Duration is in minutes.","parameters":{"type":"object","properties":{"subject_code":{"type":"string"},"task_title":{"type":"string"},"planned_minutes":{"type":"integer"}},"required":["subject_code","task_title","planned_minutes"]}}},
    {"type":"function","function":{"name":"get_active_academic_session","description":"Get the current active study session.","parameters":{"type":"object","properties":{},"required":[]}}},
    {"type":"function","function":{"name":"get_remaining_study_time","description":"Return exact remaining minutes and seconds for the active session. Use only for questions about remaining study time, never for task due dates.","parameters":{"type":"object","properties":{},"required":[]}}},
    {"type":"function","function":{"name":"end_active_academic_session","description":"End the current study session. Set ended_early true only when the user explicitly ends early.","parameters":{"type":"object","properties":{"ended_early":{"type":"boolean"},"notes":{"type":"string"}},"required":[]}}},
    {"type":"function","function":{"name":"create_alarm","description":"Create and save an alarm that will be delivered by the Raspberry Pi speaker. Use alarm_time in HH:MM 24-hour format. If date is omitted and no repeat days are chosen, schedule the next occurrence. If date or time is unclear, ask.","parameters":{"type":"object","properties":{"title":{"type":"string"},"alarm_time":{"type":"string"},"alarm_date":{"type":"string"},"repeat_days":{"type":"array","items":{"type":"string","enum":["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]}},"topic":{"type":"string"},"enabled":{"type":"boolean"}},"required":["alarm_time"]}}},
    {"type":"function","function":{"name":"list_alarms","description":"List all configured alarms and their schedules.","parameters":{"type":"object","properties":{},"required":[]}}},
    {"type":"function","function":{"name":"update_alarm","description":"Update an existing alarm by its exact alarm ID. Call list_alarms first.","parameters":{"type":"object","properties":{"alarm_id":{"type":"integer"},"title":{"type":"string"},"alarm_time":{"type":"string"},"alarm_date":{"type":"string"},"repeat_days":{"type":"array","items":{"type":"string"}},"topic":{"type":"string"},"enabled":{"type":"boolean"}},"required":["alarm_id"]}}},
    {"type":"function","function":{"name":"delete_alarm","description":"Delete an alarm by its exact ID. Call list_alarms first and only delete when explicitly asked.","parameters":{"type":"object","properties":{"alarm_id":{"type":"integer"}},"required":["alarm_id"]}}},
])

def execute_tool(name, arguments):
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return {"success": False, "error": f"Tool '{name}' is not permitted."}
    if not isinstance(arguments, dict):
        return {"success": False, "error": "Invalid tool arguments."}
    try:
        return function(**arguments)
    except (TypeError, ValueError) as error:
        return {"success": False, "error": str(error)}
    except Exception:
        return {"success": False, "error": "The tool encountered an internal error."}

initialize_database()

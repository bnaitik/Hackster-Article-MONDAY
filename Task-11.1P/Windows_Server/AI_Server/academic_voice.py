"""Voice tools for Monday's academic planner and study sessions."""
import os
import re
import json
import sqlite3
from pathlib import Path
from datetime import datetime, date, time, timedelta
from difflib import SequenceMatcher

BASE_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass
DB_PATH = Path(os.getenv("MONDAY_DB_PATH", str(BASE_DIR / "monday.db"))).expanduser().resolve()
def now(): return datetime.now().astimezone().isoformat(timespec="seconds")
def dbopen():
    c=sqlite3.connect(DB_PATH,timeout=15); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def norm(s): return " ".join(str(s or "").strip().lower().replace("-"," ").split())

def _date_value(value, label):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as error:
        raise ValueError(f"{label} must use YYYY-MM-DD format.") from error
def list_academic_structure(include_archived=False):
    with dbopen() as c:
        where="" if include_archived else "WHERE se.status='active'"
        rows=c.execute(f"""SELECT se.id semester_id,se.name semester_name,se.status,su.id subject_id,su.code subject_code,su.name subject_name,COUNT(t.id) task_count,SUM(CASE WHEN t.completed=1 THEN 1 ELSE 0 END) completed_count FROM semesters se LEFT JOIN subjects su ON su.semester_id=se.id LEFT JOIN tasks t ON t.subject_id=su.id {where} GROUP BY se.id,su.id ORDER BY se.id DESC,su.code""").fetchall()
    return {"items":[dict(r) for r in rows],"count":len(rows)}
def resolve(c, code, title):
    subs=c.execute("SELECT su.*,se.name semester_name,se.status semester_status FROM subjects su JOIN semesters se ON se.id=su.semester_id WHERE UPPER(su.code)=? ORDER BY CASE se.status WHEN 'active' THEN 0 ELSE 1 END,se.id DESC",(str(code).strip().upper(),)).fetchall()
    if not subs: return {"error":f"Subject {code} was not found."}
    active=[s for s in subs if s['semester_status']=='active']
    if len(active)==1: sub=active[0]
    elif len(subs)==1: sub=subs[0]
    else: return {"error":"This subject exists in multiple semesters. Please specify the semester.","matches":[s['semester_name'] for s in subs]}
    tasks=c.execute("SELECT * FROM tasks WHERE subject_id=? ORDER BY id DESC",(sub['id'],)).fetchall(); q=norm(title)
    exact=[t for t in tasks if norm(t['title'])==q]
    matches=exact or [t for t in tasks if q in norm(t['title']) or norm(t['title']) in q]
    if not matches:
        scored=sorted(((SequenceMatcher(None,q,norm(t['title'])).ratio(),t) for t in tasks),key=lambda x:x[0],reverse=True)
        matches=[t for score,t in scored if score>=.72][:5]
    if len(matches)==1: return {'subject':sub,'task':matches[0]}
    if matches: return {'error':'Multiple tasks match. Please say the exact task name.','matches':[t['title'] for t in matches]}
    return {'error':f"Task '{title}' was not found in {code}.",'available_tasks':[t['title'] for t in tasks]}
def create_academic_task(subject_code,title,start_date="",due_date="",notes="",priority="normal"):
    title=str(title or '').strip()
    if not title: return {'success':False,'error':'Task title cannot be empty.'}
    if priority not in ('low','normal','high'): priority='normal'
    try:
        start_date = _date_value(start_date, 'Start date')
        due_date = _date_value(due_date, 'Due date')
    except ValueError as error:
        return {'success': False, 'error': str(error)}
    if start_date and due_date and start_date > due_date:
        return {'success': False, 'error': 'Start date cannot be later than the due date.'}
    stamp=now()
    with dbopen() as c:
        subs=c.execute("SELECT su.*,se.status semester_status FROM subjects su JOIN semesters se ON se.id=su.semester_id WHERE UPPER(su.code)=? ORDER BY se.id DESC",(str(subject_code).strip().upper(),)).fetchall()
        active=[s for s in subs if s['semester_status']=='active']
        if len(active)==1: sub=active[0]
        elif len(subs)==1: sub=subs[0]
        elif not subs: return {'success':False,'error':f'Subject {subject_code} was not found.'}
        else: return {'success':False,'error':'Subject exists in multiple semesters; specify the semester.'}
        cur=c.execute("INSERT INTO tasks(title,description,due_date,priority,completed,created_at,subject_id,start_date,progress,progress_note,updated_at) VALUES(?,?,?, ?,0,?,?,?,0,'',?)",(title,str(notes or ''),str(due_date or ''),priority,stamp,sub['id'],str(start_date or ''),stamp))
        c.execute("INSERT INTO task_progress(task_id,progress,note,created_at) VALUES(?,0,'Created by voice',?)",(cur.lastrowid,stamp))
    return {'success':True,'task_id':cur.lastrowid,'subject':subject_code.upper(),'title':title}
def update_academic_task(subject_code,task_title,start_date=None,due_date=None,progress=None,progress_note=None,notes=None,priority=None):
    with dbopen() as c:
        f=resolve(c,subject_code,task_title)
        if 'error' in f: return {'success':False,**f}
        t=f['task']; d={}
        try:
            if start_date is not None: d['start_date']=_date_value(start_date, 'Start date')
            if due_date is not None: d['due_date']=_date_value(due_date, 'Due date')
        except ValueError as error:
            return {'success': False, 'error': str(error)}
        next_start = d.get('start_date', t['start_date'] or '')
        next_due = d.get('due_date', t['due_date'] or '')
        if next_start and next_due and next_start > next_due:
            return {'success': False, 'error': 'Start date cannot be later than the due date.'}
        if progress is not None:
            try: p=int(progress)
            except: return {'success':False,'error':'Progress must be 0 to 100.'}
            if not 0<=p<=100: return {'success':False,'error':'Progress must be 0 to 100.'}
            d['progress']=p; d['completed']=1 if p==100 else 0
        if progress_note is not None: d['progress_note']=str(progress_note)
        if notes is not None: d['description']=str(notes)
        if priority is not None:
            if priority not in ('low','normal','high'): return {'success':False,'error':'Priority must be low, normal or high.'}
            d['priority']=priority
        if not d: return {'success':False,'error':'No changes supplied.'}
        d['updated_at']=now(); c.execute('UPDATE tasks SET '+','.join(k+'=?' for k in d)+' WHERE id=?',(*d.values(),t['id']))
        if 'progress' in d or 'progress_note' in d:
            c.execute('INSERT INTO task_progress(task_id,progress,note,created_at) VALUES(?,?,?,?)',(t['id'],d.get('progress',t['progress'] or 0),d.get('progress_note',t['progress_note'] or ''),now()))
        updated=dict(c.execute('SELECT * FROM tasks WHERE id=?',(t['id'],)).fetchone())
    return {'success':True,'task':updated}
def get_academic_task_progress(subject_code,task_title):
    with dbopen() as c:
        f=resolve(c,subject_code,task_title)
        if 'error' in f: return {'success':False,**f}
        t=f['task']; hist=c.execute('SELECT progress,note,created_at FROM task_progress WHERE task_id=? ORDER BY id DESC LIMIT 10',(t['id'],)).fetchall()
    p=int(t['progress'] or (100 if t['completed'] else 0))
    return {'success':True,'subject':subject_code.upper(),'task':t['title'],'progress':p,'remaining':100-p,'progress_note':t['progress_note'] or '', 'due_date':t['due_date'] or 'not set','history':[dict(x) for x in hist]}
def start_academic_session(subject_code,task_title,planned_minutes):
    try: m=int(planned_minutes)
    except: return {'success':False,'error':'Specify duration in minutes.'}
    if not 1<=m<=720: return {'success':False,'error':'Duration must be 1â€“720 minutes.'}
    with dbopen() as c:
        f=resolve(c,subject_code,task_title)
        if 'error' in f: return {'success':False,**f}
        if c.execute("SELECT id FROM study_sessions WHERE status='active' LIMIT 1").fetchone(): return {'success':False,'error':'A study session is already active.'}
        start=datetime.now().astimezone(); end=datetime.fromtimestamp(start.timestamp()+m*60).astimezone().isoformat(timespec='seconds')
        cur=c.execute("INSERT INTO study_sessions(task_id,started_at,planned_minutes,planned_end,status) VALUES(?,?,?,?,'active')",(f['task']['id'],start.isoformat(timespec='seconds'),m,end))
    return {'success':True,'session_id':cur.lastrowid,'subject':subject_code.upper(),'task':f['task']['title'],'planned_minutes':m,'planned_end':end}

from datetime import datetime

def get_remaining_study_time():
    expire_overdue_sessions()
    with dbopen() as conn:
        row = conn.execute(
            "SELECT ss.*, t.title task_title, s.code subject_code "
            "FROM study_sessions ss JOIN tasks t ON t.id=ss.task_id "
            "LEFT JOIN subjects s ON s.id=t.subject_id "
            "WHERE ss.status='active' ORDER BY ss.id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            last = conn.execute(
                "SELECT ss.*, t.title task_title, s.code subject_code "
                "FROM study_sessions ss JOIN tasks t ON t.id=ss.task_id "
                "LEFT JOIN subjects s ON s.id=t.subject_id "
                "ORDER BY ss.id DESC LIMIT 1"
            ).fetchone()
            if last and last["status"] == "completed":
                return {
                    "success": True, "active": False, "completed": True,
                    "task": last["task_title"], "subject": last["subject_code"],
                    "planned_end": last["planned_end"]
                }
            return {
                "success": False, "active": False,
                "message": "There is no active study session."
            }

    deadline = datetime.fromisoformat(row["planned_end"])
    remaining = max(0, int((deadline - datetime.now().astimezone()).total_seconds()))
    minutes, seconds = divmod(remaining, 60)
    return {
        "success": True, "active": True, "completed": False,
        "task": row["task_title"], "subject": row["subject_code"],
        "planned_minutes": row["planned_minutes"],
        "planned_end": row["planned_end"],
        "remaining_seconds": remaining,
        "remaining_display": str(minutes) + " minutes and " + str(seconds) + " seconds"
    }

def get_active_academic_session():
    expire_overdue_sessions()
    with dbopen() as c: r=c.execute("SELECT ss.*,t.title task_title,s.code subject_code FROM study_sessions ss JOIN tasks t ON t.id=ss.task_id LEFT JOIN subjects s ON s.id=t.subject_id WHERE ss.status='active' ORDER BY ss.id DESC LIMIT 1").fetchone()
    return {'active':dict(r) if r else None}
def end_active_academic_session(ended_early=False,notes=''):
    expire_overdue_sessions()
    with dbopen() as c:
        r=c.execute("SELECT * FROM study_sessions WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        if not r: return {'success':False,'error':'There is no active study session.'}
        c.execute("UPDATE study_sessions SET ended_at=?,status=?,notes=? WHERE id=?",(now(),'ended_early' if ended_early else 'completed',str(notes or ''),r['id']))
        result=dict(c.execute("SELECT ss.*,t.title task_title,s.code subject_code FROM study_sessions ss JOIN tasks t ON t.id=ss.task_id LEFT JOIN subjects s ON s.id=t.subject_id WHERE ss.id=?",(r['id'],)).fetchone())
    return {'success':True,'session':result}

def list_academic_tasks(subject_code):
    with dbopen() as c:
        subs = c.execute(
            "SELECT su.*, se.name semester_name, se.status semester_status "
            "FROM subjects su JOIN semesters se ON se.id=su.semester_id "
            "WHERE UPPER(su.code)=? ORDER BY CASE se.status WHEN 'active' THEN 0 ELSE 1 END,se.id DESC",
            (str(subject_code).strip().upper(),)
        ).fetchall()
        active = [x for x in subs if x['semester_status'] == 'active']
        if len(active) == 1:
            sub = active[0]
        elif len(subs) == 1:
            sub = subs[0]
        elif not subs:
            return {'success': False, 'error': f'Subject {subject_code} was not found.'}
        else:
            return {'success': False, 'error': 'Subject exists in multiple semesters; specify the semester.'}
        rows = c.execute(
            "SELECT id,title,start_date,due_date,priority,completed,progress,description "
            "FROM tasks WHERE subject_id=? ORDER BY CASE WHEN due_date='' THEN 1 ELSE 0 END,due_date,id DESC",
            (sub['id'],)
        ).fetchall()
    return {'success': True, 'subject': str(subject_code).upper(), 'tasks': [dict(x) for x in rows], 'count': len(rows)}

def delete_academic_task(subject_code, task_title):
    with dbopen() as c:
        found = resolve(c, subject_code, task_title)
        if 'error' in found:
            return {'success': False, **found}
        task = found['task']
        task_id = int(task['id'])
        for table in ('task_progress', 'study_sessions'):
            exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists:
                c.execute(f'DELETE FROM {table} WHERE task_id=?', (task_id,))
        c.execute('DELETE FROM tasks WHERE id=?', (task_id,))
    return {'success': True, 'task_id': task_id, 'subject': str(subject_code).upper(), 'title': task['title'], 'message': 'Task deleted successfully.'}

_ALARM_DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')

def _normalise_alarm_time(value):
    raw = str(value or '').strip().lower().replace('.', '')
    match = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', raw)
    if not match:
        raise ValueError('Alarm time must look like 07:30, 7 AM, or 7:30 PM.')
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = match.group(3)
    if minute > 59:
        raise ValueError('Alarm minutes must be between 00 and 59.')
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError('12-hour alarm time must use an hour from 1 to 12.')
        hour = hour % 12 + (12 if ampm == 'pm' else 0)
    elif not 0 <= hour <= 23:
        raise ValueError('24-hour alarm time must use an hour from 0 to 23.')
    return f'{hour:02d}:{minute:02d}'

def _alarm_date_or_next(alarm_time, alarm_date, repeat_days):
    raw = str(alarm_date or '').strip()
    if raw:
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            raise ValueError('Alarm date must use YYYY-MM-DD format.')
    if repeat_days:
        return ''
    now_local = datetime.now().astimezone()
    today = now_local.date()
    scheduled = datetime.combine(today, time.fromisoformat(alarm_time), tzinfo=now_local.tzinfo)
    return (today if scheduled > now_local else today + timedelta(days=1)).isoformat()

def _normalise_alarm_days(days):
    if days is None:
        return []
    if isinstance(days, str):
        days = [x.strip() for x in re.split(r'[,; ]+', days) if x.strip()]
    if not isinstance(days, (list, tuple, set)):
        raise ValueError('Repeat days must be a list of weekdays.')
    lookup = {d.lower(): d for d in _ALARM_DAYS}
    lookup.update({d.lower()[:3]: d for d in _ALARM_DAYS})
    result = []
    for day in days:
        canonical = lookup.get(str(day).strip().lower())
        if not canonical:
            raise ValueError('Repeat days must be Mon, Tue, Wed, Thu, Fri, Sat, or Sun.')
        if canonical not in result:
            result.append(canonical)
    return result

def create_alarm(title='Monday alarm', alarm_time='', alarm_date='', repeat_days=None, topic='', enabled=True):
    title = str(title or 'Monday alarm').strip() or 'Monday alarm'
    try:
        alarm_time = _normalise_alarm_time(alarm_time)
        days = _normalise_alarm_days(repeat_days)
        alarm_date = _alarm_date_or_next(alarm_time, alarm_date, days)
    except ValueError as error:
        return {'success': False, 'error': str(error)}
    stamp = now()
    with dbopen() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS alarms (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            alarm_time TEXT NOT NULL, alarm_date TEXT DEFAULT '',
            repeat_days TEXT DEFAULT '[]', topic TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS alarm_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, alarm_id INTEGER,
            title TEXT NOT NULL, scheduled_for TEXT NOT NULL,
            triggered_at TEXT DEFAULT '', dismissed_at TEXT DEFAULT '',
            trivia_total INTEGER DEFAULT 0, trivia_correct INTEGER DEFAULT 0,
            outcome TEXT DEFAULT 'scheduled')""")
        cur = c.execute(
            "INSERT INTO alarms(title,alarm_time,alarm_date,repeat_days,topic,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (title, alarm_time, alarm_date, json.dumps(days), str(topic or ''), 1 if enabled else 0, stamp, stamp)
        )
        return {'success': True, 'alarm_id': cur.lastrowid, 'title': title,
                'alarm_time': alarm_time, 'alarm_date': alarm_date,
                'repeat_days': days, 'topic': str(topic or ''), 'enabled': bool(enabled),
                'message': 'Alarm saved successfully.'}

def list_alarms():
    with dbopen() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alarms'").fetchone():
            return {'success': True, 'alarms': [], 'count': 0}
        rows = c.execute('SELECT * FROM alarms ORDER BY enabled DESC,alarm_time').fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item['repeat_days'] = json.loads(item.get('repeat_days') or '[]')
        except (TypeError, ValueError):
            item['repeat_days'] = []
        item['enabled'] = bool(item.get('enabled'))
        result.append(item)
    return {'success': True, 'alarms': result, 'count': len(result)}

def update_alarm(alarm_id, title=None, alarm_time=None, alarm_date=None, repeat_days=None, topic=None, enabled=None):
    try:
        alarm_id = int(alarm_id)
    except (TypeError, ValueError):
        return {'success': False, 'error': 'A valid alarm ID is required.'}
    changes = {}
    if title is not None:
        value = str(title).strip()
        if not value:
            return {'success': False, 'error': 'Alarm title cannot be empty.'}
        changes['title'] = value
    if alarm_time is not None:
        try:
            changes['alarm_time'] = _normalise_alarm_time(alarm_time)
        except ValueError as error:
            return {'success': False, 'error': str(error)}
    if repeat_days is not None:
        try:
            changes['repeat_days'] = json.dumps(_normalise_alarm_days(repeat_days))
        except ValueError as error:
            return {'success': False, 'error': str(error)}
    if alarm_date is not None:
        raw = str(alarm_date or '').strip()
        if raw:
            try:
                date.fromisoformat(raw)
            except ValueError:
                return {'success': False, 'error': 'Alarm date must use YYYY-MM-DD format.'}
        changes['alarm_date'] = raw
    if topic is not None:
        changes['topic'] = str(topic)
    if enabled is not None:
        changes['enabled'] = 1 if enabled else 0
    if not changes:
        return {'success': False, 'error': 'No alarm changes were supplied.'}
    changes['updated_at'] = now()
    with dbopen() as c:
        row = c.execute('SELECT * FROM alarms WHERE id=?', (alarm_id,)).fetchone()
        if row is None:
            return {'success': False, 'error': f'No alarm found with ID {alarm_id}.'}
        if 'alarm_time' not in changes:
            changes['alarm_time'] = row['alarm_time']
        if 'repeat_days' not in changes:
            try:
                days = json.loads(row['repeat_days'] or '[]')
            except (TypeError, ValueError):
                days = []
        else:
            days = json.loads(changes['repeat_days'])
        if 'alarm_date' not in changes and not days and not row['alarm_date']:
            changes['alarm_date'] = _alarm_date_or_next(changes['alarm_time'], '', days)
        c.execute('UPDATE alarms SET ' + ','.join(k+'=?' for k in changes) + ' WHERE id=?', (*changes.values(), alarm_id))
        updated = dict(c.execute('SELECT * FROM alarms WHERE id=?', (alarm_id,)).fetchone())
    updated['repeat_days'] = json.loads(updated.get('repeat_days') or '[]')
    updated['enabled'] = bool(updated.get('enabled'))
    return {'success': True, 'alarm': updated, 'message': 'Alarm updated successfully.'}

def delete_alarm(alarm_id):
    try:
        alarm_id = int(alarm_id)
    except (TypeError, ValueError):
        return {'success': False, 'error': 'A valid alarm ID is required.'}
    with dbopen() as c:
        row = c.execute('SELECT id,title FROM alarms WHERE id=?', (alarm_id,)).fetchone()
        if row is None:
            return {'success': False, 'error': f'No alarm found with ID {alarm_id}.'}
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alarm_history'").fetchone():
            c.execute('UPDATE alarm_history SET alarm_id=NULL WHERE alarm_id=?', (alarm_id,))
        c.execute('DELETE FROM alarms WHERE id=?', (alarm_id,))
    return {'success': True, 'alarm_id': alarm_id, 'title': row['title'], 'message': 'Alarm deleted successfully.'}

import threading
import time as _time

def expire_overdue_sessions():
    current = datetime.now().astimezone()
    expired_ids = []
    with dbopen() as conn:
        rows = conn.execute("SELECT id, planned_end FROM study_sessions WHERE status='active'").fetchall()
        for row in rows:
            try:
                deadline = datetime.fromisoformat(row["planned_end"])
            except (TypeError, ValueError):
                continue
            if deadline <= current:
                conn.execute(
                    "UPDATE study_sessions SET status='completed', ended_at=? WHERE id=? AND status='active'",
                    (current.isoformat(timespec='seconds'), row["id"])
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    expired_ids.append(row["id"])
    return expired_ids

def _monday_session_expiry_worker():
    while True:
        try:
            expire_overdue_sessions()
        except Exception:
            pass
        _time.sleep(1)

threading.Thread(target=_monday_session_expiry_worker, name="monday-session-expiry", daemon=True).start()

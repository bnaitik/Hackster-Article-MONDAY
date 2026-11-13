import os
import asyncio
import io
import wave
import urllib.request
import uuid

import json

import logging
import re

import secrets

from contextlib import asynccontextmanager

import ollama

from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Header, Depends, WebSocket, WebSocketDisconnect

from pydantic import BaseModel, Field

from tools import TOOL_DEFINITIONS, execute_tool

load_dotenv()

MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")

API_TOKEN = os.getenv("MONDAY_API_TOKEN", "")
STT_URL = os.getenv("MONDAY_STT_URL", "http://127.0.0.1:8001/v1/transcribe")

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s | %(levelname)s | %(message)s"

)

logger = logging.getLogger("MondayServer")

client = ollama.AsyncClient(host=OLLAMA_HOST)

SYSTEM_PROMPT = """/no_think

You are Monday, the central AI assistant for a physical Smart Desk Bot.

You are both a capable general chatbot and a tool-using assistant.

IMPORTANT RESPONSE RULES:

- Give the user only your final answer.

- Never include internal reasoning, private notes, analysis,

  or descriptions of how you are thinking.

- Do not narrate your planning process.

- Do not write phrases such as "Let me think", "The user is asking",

  or "First, I need to".

- Answer naturally and directly.

GENERAL CONVERSATION:

Answer questions about science, engineering, programming, studies,

productivity, everyday topics and general knowledge.

Explain concepts clearly and naturally.

Do not call tools for ordinary questions.

For factual questions, give a useful direct answer without unnecessary

introductions or repeated conclusions.

TASKS AND REMINDERS:
All academic tasks MUST belong to a subject. Never create unassigned/global tasks.
If the user does not name a subject, ask which subject before creating a task.
For task creation, use create_academic_task with the exact subject code and title.
Before changing or deleting a task, identify the exact task and its subject. If more than one task matches, ask which one.
For a requested task start-date or due-date change, ALWAYS use update_academic_task and pass only the requested field(s). Convert the date the user stated to YYYY-MM-DD. Do NOT call get_current_time and do NOT answer with today's date when the user is changing a task date. Preserve all other fields.
Only delete a task when the user explicitly asks. Never claim success unless the tool returns success=true.
Use get_academic_task_progress for task progress questions.

ALARMS:
When the user asks to set/create/schedule an alarm, create it with create_alarm. If they ask to see alarms, use list_alarms. Update/delete only after identifying the exact alarm with list_alarms. Use local 24-hour HH:MM. If the time is unclear, ask. If no date or repeat days are specified, create_alarm schedules the next occurrence. Never say an alarm is saved without calling the alarm tool.
HARDWARE:

You can control the physical desk light through the Raspberry Pi.

The Pi validates and executes physical commands.

Available hardware tools:

set_light_brightness

adjust_light_brightness

turn_light_on

turn_light_off

set_light_auto

get_hardware_status

Interpret natural language:

"A little brighter" means a small positive adjustment.

"Too bright" means reduce brightness.

"Set it to 65 percent" means exact brightness.

"Let the sensor handle it" means automatic mode.

Never invent hardware capabilities.

Never claim physical execution succeeded before the device confirms it.

ACADEMIC PLANNER AND STUDY SESSIONS:
Use subject-scoped academic tools for all course tasks. Never use generic create_task for college assignments.
For task date changes, use update_academic_task, never get_current_time. The due date of an assignment is not today's date.

STUDY SESSIONS:
- When starting a named task for a duration, call start_academic_session and convert hours to minutes.
- When asked which task is active, call get_active_academic_session.
- When asked how much time remains or when the session ends, ALWAYS call get_remaining_study_time. Never substitute the current clock time.
- When the user ends a session, call end_active_academic_session; set ended_early=true only when they explicitly end early.
- The Raspberry Pi provides spoken motivational check-ins during active sessions. Do not claim a check-in was delivered unless the device has confirmed it.
TIME:

Use get_current_time for actual current date and time.

Do not invent the current time.

Keep spoken answers concise by default, but answer the actual question.

Ask for clarification when necessary.

"""

def clean_model_text(text):

    if not text:

        return ""

    text = str(text).strip()

    lower = text.lower()

    if "</think>" in lower:

        position = lower.rfind("</think>")

        return text[position + len("</think>"):].strip()

    if "<think>" in lower:

        return ""

    return text

_MONTHS = {name.lower(): index for index, name in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
_MONTHS.update({name[:3].lower(): index for name, index in list(_MONTHS.items()) if len(name) > 3})
_WEEKDAYS = {name.lower(): index for index, name in enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))}

def _parse_monday_date(raw):
    from datetime import date, timedelta
    value = str(raw or "").strip().lower().replace(",", " ")
    today = date.today()
    if value == "today":
        return today.isoformat()
    if value == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if value == "day after tomorrow":
        return (today + timedelta(days=2)).isoformat()
    if value.startswith("next ") and value[5:] in _WEEKDAYS:
        delta = (_WEEKDAYS[value[5:]] - today.weekday()) % 7 or 7
        return (today + timedelta(days=delta)).isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    patterns = (("%d %B %Y", True), ("%d %b %Y", True), ("%B %d %Y", True), ("%b %d %Y", True),
                ("%d %B", False), ("%d %b", False), ("%B %d", False), ("%b %d", False))
    for fmt, has_year in patterns:
        try:
            parsed = date.strptime(value, fmt) if hasattr(date, "strptime") else None
        except ValueError:
            parsed = None
        if parsed is not None:
            year = parsed.year
            if not has_year:
                year = today.year
                parsed = parsed.replace(year=year)
                if parsed < today:
                    parsed = parsed.replace(year=year + 1)
            return parsed.isoformat()
    from datetime import datetime
    for fmt, has_year in patterns:
        try:
            parsed_dt = datetime.strptime(value, fmt)
            year = parsed_dt.year if has_year else today.year
            parsed = date(year, parsed_dt.month, parsed_dt.day)
            if not has_year and parsed < today:
                parsed = date(year + 1, parsed_dt.month, parsed_dt.day)
            return parsed.isoformat()
        except ValueError:
            continue
    return None

def _date_token(text):
    month_names = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    pattern = rf"(?i)\b(day after tomorrow|tomorrow|today|next\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}(?:st|nd|rd|th)?\s+{month_names}(?:\s+\d{{4}})?|{month_names}\s+\d{{1,2}}(?:,?\s+\d{{4}})?)\b"
    match = re.search(pattern, text)
    if not match:
        return None, None
    raw = match.group(1)
    raw = re.sub(r"(?i)(\d)(st|nd|rd|th)\b", r"\1", raw)
    return raw, _parse_monday_date(raw)

def _normal_task_name(value):
    value = str(value or "").lower().strip(" .,!?")
    value = re.sub(r"^(?:(?:the|my)\s+)?(?:task|assignment)\s+(?:called\s+)?", "", value)
    value = re.sub(r"^(?:the|my)\s+", "", value)
    value = re.sub(r"\s+task$", "", value)
    value = re.sub(r"\s+in\s+[a-z]{2,5}\d{2,4}$", "", value)
    return " ".join(value.split())

def _maybe_update_task_date(text, session_id):
    lower = str(text or "").lower()
    if not re.search(r"\b(change|update|move|shift|set|reschedule)\b", lower):
        return None
    field_match = re.search(r"\b(due\s+date|deadline|start\s+date)\b", lower)
    if not field_match:
        return None
    field = "start_date" if field_match.group(1).lower() == "start date" else "due_date"
    field_label = "start date" if field == "start_date" else "due date"
    raw_date, iso_date = _date_token(text)
    if not raw_date:
        return None
    if not iso_date:
        return MessageResponse(session_id=session_id, response="I couldn't understand that date. Please say it as 20 November 2026 or 2026-11-20.", model=MODEL)

    date_suffix = r"\s+(?:to|as|until|on)\s+" + re.escape(raw_date)
    patterns = [
        r"(?is)(?:change|update|move|shift|set|reschedule)\s+(?:the\s+)?(?:due\s+date|deadline|start\s+date)\s+(?:of|for)\s+(.+?)" + date_suffix,
        r"(?is)(?:change|update|move|shift|set|reschedule)\s+(?:the\s+)?(.+?)(?:'s)?\s+(?:due\s+date|deadline|start\s+date)" + date_suffix,
    ]
    task_query = None
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            task_query = _normal_task_name(match.group(1))
            break
    if not task_query or task_query in {"it", "its", "this", "that", "the task", "my task"}:
        return None

    subject_match = re.search(r"\b([A-Z]{2,5}\d{2,4})\b", text, re.IGNORECASE)
    subject_code = subject_match.group(1).upper() if subject_match else None
    task_result = execute_tool("list_tasks", {"include_completed": True})
    tasks = [item for item in task_result.get("tasks", []) if item.get("subject_code")]
    if subject_code:
        tasks = [item for item in tasks if str(item.get("subject_code", "")).upper() == subject_code]
    q = task_query
    exact = [item for item in tasks if _normal_task_name(item.get("title")) == q]
    matches = exact or [item for item in tasks if q in _normal_task_name(item.get("title")) or _normal_task_name(item.get("title")) in q]
    if len(matches) != 1:
        if not matches:
            answer = f"I couldn't find a subject task matching '{task_query}'. Please give me its exact task title and subject code."
        else:
            options = ", ".join(f"{item['title']} ({item['subject_code']})" for item in matches[:5])
            answer = f"I found more than one matching task: {options}. Which one should I update?"
        return MessageResponse(session_id=session_id, response=answer, model=MODEL)

    task = matches[0]
    arguments = {"subject_code": task["subject_code"], "task_title": task["title"], field: iso_date}
    result = execute_tool("update_academic_task", arguments)
    if result.get("success"):
        answer = f"Done. I updated the {field_label} for {task['title']} ({task['subject_code']}) to {iso_date}."
    else:
        answer = f"I couldn't update that {field_label}: {result.get('error', 'unknown error')}"
    return MessageResponse(session_id=session_id, response=answer, model=MODEL,
                           actions=[{"tool": "update_academic_task", "arguments": arguments, "result": result}])

_ALARM_HOUR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_ALARM_MINUTE_WORDS = {
    "oh": "0", "zero": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "fifteen": "15", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50",
}

def _extract_alarm_time(text):
    """Return HH:MM only when the spoken time is sufficiently explicit."""
    value = str(text or "").lower().replace(".", " ")

    explicit = re.search(
        r"\b(?P<hour>\d{1,2})(?::(?P<minute>[0-5]\d))?\s*(?P<ampm>a\s*m|p\s*m|am|pm)\b",
        value,
    )
    if explicit:
        hour = int(explicit.group("hour"))
        minute = int(explicit.group("minute") or 0)
        ampm = explicit.group("ampm").replace(" ", "")
        if not 1 <= hour <= 12:
            return None, "Please give the alarm time using an hour from 1 to 12 and AM or PM."
        hour = hour % 12 + (12 if ampm == "pm" else 0)
        return f"{hour:02d}:{minute:02d}", None

    clock = re.search(r"\b(?P<hour>\d{1,2}):(?P<minute>[0-5]\d)\b", value)
    if clock:
        hour, minute = int(clock.group("hour")), int(clock.group("minute"))
        if hour > 23:
            return None, "Please give the alarm time in 24-hour format, such as 16:30."
        return f"{hour:02d}:{minute:02d}", None

    bare = re.search(r"\b(?:at|for|around|by)\s+(\d{1,2})(?:\s|$)", value)
    if bare:
        hour = int(bare.group(1))
        if 1 <= hour <= 12:
            return None, f"Did you mean {hour}:00 AM or {hour}:00 PM?"
        if 0 <= hour <= 23:
            return f"{hour:02d}:00", None
        return None, "Please tell me a valid alarm time."

    word_pattern = re.search(
        r"\b(?:at|for|around|by)\s+"
        r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        r"(?:\s+(oh|zero|one|two|three|four|five|six|seven|eight|nine))?"
        r"(?:\s+(fifteen|twenty|thirty|forty|fifty)(?:\s+(five))?)?"
        r"\s*(in the morning|in the afternoon|in the evening|at night|am|pm)\b",
        value,
    )
    if word_pattern:
        hour = _ALARM_HOUR_WORDS[word_pattern.group(1)]
        minute = 0
        small, tens, five = word_pattern.group(2), word_pattern.group(3), word_pattern.group(4)
        if tens:
            minute = int(_ALARM_MINUTE_WORDS[tens]) + (5 if five else 0)
        elif small:
            minute = int(_ALARM_MINUTE_WORDS[small])
        period = word_pattern.group(5)
        if period in ("pm", "in the afternoon", "in the evening", "at night"):
            hour = hour % 12 + 12
            if period == "at night" and hour == 12:
                hour = 0
        else:
            hour = hour % 12
        return f"{hour:02d}:{minute:02d}", None

    return None, "What time should I set the alarm for? Please include AM or PM."

def _maybe_create_alarm(text, session_id):
    lower = str(text or "").lower()
    if not re.search(r"\b(set|create|add|schedule)\b", lower) or not re.search(r"\b(alarm|alarms)\b", lower):
        return None
    if re.search(r"\b(delete|remove|cancel|disable|turn\s+off)\b", lower):
        return None

    alarm_time, clarification = _extract_alarm_time(text)
    if clarification:
        return MessageResponse(session_id=session_id, response=clarification, model=MODEL)

    days = []
    if re.search(r"\b(every\s+day|daily)\b", lower):
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    elif re.search(r"\bweekdays\b", lower):
        days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    elif re.search(r"\bweekends\b", lower):
        days = ["Sat", "Sun"]
    else:
        for full, short in (("monday","Mon"),("tuesday","Tue"),("wednesday","Wed"),("thursday","Thu"),("friday","Fri"),("saturday","Sat"),("sunday","Sun")):
            if re.search(r"\b" + full + r"\b", lower):
                days.append(short)
    raw_date, iso_date = _date_token(text)
    alarm_date = iso_date or ""
    title_match = re.search(r"\b(?:called|named)\s+(.+?)(?=\s+(?:at|for|on|every)\b|$)", text, re.IGNORECASE)
    title = title_match.group(1).strip(" .,!?") if title_match else "Monday alarm"
    arguments = {"title": title, "alarm_time": alarm_time, "alarm_date": alarm_date, "repeat_days": days}
    result = execute_tool("create_alarm", arguments)
    if result.get("success"):
        schedule = result.get("alarm_date") or (", ".join(result.get("repeat_days") or []) if result.get("repeat_days") else "next occurrence")
        answer = f"Done. I've set {title} for {alarm_time} ({schedule}). Your Raspberry Pi must be powered on and connected for it to sound."
    else:
        answer = f"I couldn't save that alarm: {result.get('error', 'unknown error')}"
    return MessageResponse(session_id=session_id, response=answer, model=MODEL,
                           actions=[{"tool": "create_alarm", "arguments": arguments, "result": result}])

class MessageRequest(BaseModel):

    text: str = Field(min_length=1, max_length=4000)

    session_id: str = Field(default="default", max_length=100)

class MessageResponse(BaseModel):

    session_id: str

    response: str

    model: str

    actions: list[dict] = Field(default_factory=list)

def verify_api_token(x_api_key: str = Header(default="")):

    if not API_TOKEN:

        raise HTTPException(

            status_code=503,

            detail="Monday API token is not configured."

        )

    if not secrets.compare_digest(x_api_key, API_TOKEN):

        raise HTTPException(

            status_code=401,

            detail="Invalid API token."

        )

@asynccontextmanager

async def lifespan(app: FastAPI):

    logger.info("Monday AI server starting with model %s", MODEL)

    try:

        await client.list()

        logger.info("Ollama connection successful")

    except Exception:

        logger.exception("Ollama connection failed")

    yield

    logger.info("Monday AI server shutting down")

app = FastAPI(

    title="Monday AI Server",

    description="Central intelligence for Monday Smart Desk Bot",

    version="1.2.0",

    lifespan=lifespan

)

@app.get("/")

async def root():

    return {

        "name": "Monday AI Server",

        "status": "online",

        "model": MODEL

    }

@app.get("/health")

async def health():

    try:

        await client.list()

        return {

            "status": "healthy",

            "ollama": "connected",

            "model": MODEL

        }

    except Exception as error:

        raise HTTPException(status_code=503, detail=str(error))

@app.get("/v1/study-session/status", dependencies=[Depends(verify_api_token)])
async def study_session_status():
    """Authenticated status endpoint used by the Pi reminder scheduler."""
    result = execute_tool("get_remaining_study_time", {})
    return result

@app.post(
    "/v1/assistant/message",
    response_model=MessageResponse,
    dependencies=[Depends(verify_api_token)]
)
async def assistant_message(request: MessageRequest):
    user_text = request.text.strip()
    session_id = request.session_id.strip()

    if not user_text or not session_id:
        raise HTTPException(status_code=400, detail="Message and session ID cannot be empty.")

    direct_task_date = _maybe_update_task_date(user_text, session_id)
    if direct_task_date is not None:
        return direct_task_date
    direct_alarm = _maybe_create_alarm(user_text, session_id)
    if direct_alarm is not None:
        return direct_alarm

    end_session_pattern = (
        r"\\b(end|stop|finish|cancel)\\b.{0,35}\\b(study\\s+)?(session|timer)\\b"
        r"|\\b(study\\s+)?(session|timer)\\b.{0,35}\\b(end|stop|finish|cancel)\\b"
        r"|\\b(i am done|im done|done with|finished with)\\b.{0,25}\\b(study\\s+)?(session|timer)\\b"
    )
    if re.search(end_session_pattern, user_text, re.IGNORECASE):
        end_result = execute_tool("end_active_academic_session", {
            "ended_early": True,
            "notes": "Ended by voice command"
        })
        if end_result.get("success"):
            session = end_result.get("session", {})
            answer = "I've ended your study session for " + str(session.get("task_title", "your task")) + "."
        else:
            answer = "There isn't an active study session to end."
        return MessageResponse(
            session_id=session_id, response=answer, model=MODEL,
            actions=[{"tool": "end_active_academic_session", "arguments": {"ended_early": True}, "result": end_result}]
        )

    timer_pattern = (
        r"\\b(how\\s+much\\s+time|time\\s+(left|remaining)|"
        r"how\\s+long\\s+(is\\s+)?left|how\\s+long\\s+do\\s+i\\s+have|"
        r"(remaining|left)\\s+time|when\\s+(will|does|is)\\s+"
        r"(my\\s+)?(study\\s+)?(session|timer)\\s+(end|finish))\\b"
    )
    if re.search(timer_pattern, user_text, re.IGNORECASE):
        timer_result = execute_tool("get_remaining_study_time", {})
        if timer_result.get("active"):
            answer = (
                "You have " + timer_result["remaining_display"] +
                " remaining for " + str(timer_result.get("task", "your task")) +
                " in " + str(timer_result.get("subject", "your subject")) + "."
            )
        elif timer_result.get("completed"):
            if timer_result.get("status") == "ended_early":
                answer = (
                    "Your study session for " +
                    str(timer_result.get("task", "your task")) +
                    " has ended."
                )
            else:
                answer = (
                    "Your study session for " +
                    str(timer_result.get("task", "your task")) +
                    " has finished. Time is up."
                )
        else:
            answer = "You don't have an active study session right now."
        return MessageResponse(
            session_id=session_id, response=answer, model=MODEL,
            actions=[{"tool": "get_remaining_study_time", "arguments": {}, "result": timer_result}]
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text}
    ]
    executed_actions = []

    try:
        for _ in range(4):
            result = await client.chat(
                model=MODEL,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                think=False,
                keep_alive=-1,
                options={
                    "temperature": 0.2,
                    "num_predict": 256,
                    "num_ctx": 4096
                }
            )

            assistant_message = result.message
            tool_calls = assistant_message.tool_calls or []
            response_text = clean_model_text(assistant_message.content or "")

            if not tool_calls:
                if not response_text:
                    response_text = "Sorry, I couldn't produce a clear answer. Please try again."

                return MessageResponse(
                    session_id=session_id,
                    response=response_text,
                    model=MODEL,
                    actions=executed_actions
                )

            assistant_record = {
                "role": "assistant",
                "content": response_text,
                "tool_calls": [
                    {
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments or {}
                        }
                    }
                    for call in tool_calls
                ]
            }
            messages.append(assistant_record)

            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                arguments = tool_call.function.arguments or {}
                logger.info("Executing tool: %s", tool_name)
                tool_result = execute_tool(tool_name, arguments)

                executed_actions.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": tool_result
                })

                messages.append({
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": json.dumps(tool_result, default=str)
                })

        raise HTTPException(status_code=502, detail="Maximum tool execution steps reached.")

    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Assistant request failed")
        raise HTTPException(status_code=502, detail=f"AI inference failed: {str(error)}")

VOICE_TOOL_ROUTE = re.compile(
    r"\b(alarm|alarms|wake\s+me|study\s+session|study\s+timer|session|timer|task|tasks|subject|subjects|"
    r"assignment|deadline|remind|reminder|todo|to-do|"
    r"task\s+list|list\s+my\s+tasks|add\s+(a\s+)?task|complete\s+(a\s+)?task|"
    r"time\s+(left|remaining)|how\s+much\s+time\s+(do\s+i\s+have|is\s+left|remaining)|"
    r"how\s+long\s+(do\s+i\s+have|is\s+left)|end\s+my\s+session|"
    r"what\s+time\s+is\s+it|what'?s\s+the\s+time|tell\s+me\s+the\s+time|current\s+time|"
    r"today'?s\s+date|what\s+date\s+is\s+it|"
    r"desk\s+light|brightness|lighting|lamp|hardware\s+status|"
    r"(turn|switch|set|adjust|dim|brighten|brighter|dimmer|darken|increase|decrease)\b.{0,30}\b(light|lamp)\b)\b",
    re.IGNORECASE,
)

def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()

def _transcribe_streamed_wav(wav_bytes: bytes) -> str:
    boundary = "----MondayStream" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="audio"; filename="monday-stream.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("ascii") + wav_bytes + f"\r\n--{boundary}--\r\n".encode("ascii")
    request = urllib.request.Request(
        STT_URL,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-API-Key": API_TOKEN,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    transcript = str(payload.get("text", "")).strip()
    if not transcript:
        raise ValueError("Whisper returned an empty transcript.")
    transcript = re.sub(r"^\s*monday[\s,.:;-]*", "", transcript, flags=re.IGNORECASE).strip()
    if not transcript:
        raise ValueError("No command remained after removing the wake word.")
    return transcript

async def _send_streamed_assistant_response(websocket: WebSocket, text: str, session_id: str):
    if VOICE_TOOL_ROUTE.search(text):
        result = await assistant_message(MessageRequest(text=text, session_id=session_id))
        payload = result.model_dump() if hasattr(result, "model_dump") else result.dict()
        if payload.get("actions"):
            await websocket.send_json({"type": "actions", "actions": payload["actions"]})
        answer = payload.get("response", "") or "I couldn't formulate a response."
        await websocket.send_json({"type": "text", "text": answer})
        await websocket.send_json({"type": "done", "model": payload.get("model", MODEL)})
        return

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    raw_answer = ""
    sent_length = 0
    started = asyncio.get_running_loop().time()
    first_token_ms = None
    try:
        stream = await client.chat(
            model=MODEL,
            messages=messages,
            think=False,
            keep_alive=-1,
            stream=True,
            options={"temperature": 0.2, "num_predict": 256, "num_ctx": 4096},
        )
        async for part in stream:
            message = getattr(part, "message", None)
            if message is None and isinstance(part, dict):
                message = part.get("message", {})
            content = (
                message.get("content", "") if isinstance(message, dict)
                else getattr(message, "content", "")
            ) or ""
            if not content:
                continue
            raw_answer += content
            cleaned = clean_model_text(raw_answer)
            if cleaned and len(cleaned) > sent_length:
                delta = cleaned[sent_length:]
                sent_length = len(cleaned)
                if first_token_ms is None:
                    first_token_ms = (asyncio.get_running_loop().time() - started) * 1000
                await websocket.send_json({"type": "text", "text": delta})

        final_answer = clean_model_text(raw_answer)
        if not final_answer:
            raise ValueError("Ollama returned an empty streamed response.")
        await websocket.send_json({
            "type": "stats",
            "first_token_ms": round(first_token_ms or 0, 1),
            "generation_ms": round((asyncio.get_running_loop().time() - started) * 1000, 1),
        })
        await websocket.send_json({"type": "done", "model": MODEL})
    except Exception:
        logger.exception("Ollama streaming failed.")
        if sent_length:
            await websocket.send_json({"type": "text", "text": " I had trouble finishing that response."})
            await websocket.send_json({"type": "done", "model": MODEL, "partial": True})
            return
        result = await assistant_message(MessageRequest(text=text, session_id=session_id))
        payload = result.model_dump() if hasattr(result, "model_dump") else result.dict()
        if payload.get("actions"):
            await websocket.send_json({"type": "actions", "actions": payload["actions"]})
        await websocket.send_json({"type": "text", "text": payload.get("response", "Sorry, I couldn't answer that.")})
        await websocket.send_json({"type": "done", "model": payload.get("model", MODEL), "fallback": True})

@app.websocket("/v1/voice/stream")
async def monday_voice_stream(websocket: WebSocket):
    await websocket.accept()
    try:
        hello = await websocket.receive_json()
        token = str(hello.get("token", ""))
        if not API_TOKEN or not secrets.compare_digest(token, API_TOKEN):
            await websocket.send_json({"type": "error", "message": "Unauthorized WebSocket session."})
            await websocket.close(code=1008)
            return
        if hello.get("type") != "start" or int(hello.get("sample_rate", 0)) != 16000:
            await websocket.send_json({"type": "error", "message": "Expected 16 kHz mono PCM stream."})
            await websocket.close(code=1003)
            return

        session_id = str(hello.get("session_id", "default"))[:100]
        audio = bytearray()
        max_bytes = 16_000 * 2 * 95
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            packet = message.get("bytes")
            if packet is not None:
                audio.extend(packet)
                if len(audio) > max_bytes:
                    await websocket.send_json({"type": "error", "message": "Voice command exceeded 95 seconds."})
                    await websocket.close(code=1009)
                    return
                continue
            text_message = message.get("text")
            if text_message:
                control = json.loads(text_message)
                if control.get("type") == "cancel":
                    await websocket.send_json({"type": "done", "cancelled": True})
                    await websocket.close()
                    return
                if control.get("type") == "end":
                    break

        if not audio:
            await websocket.send_json({"type": "error", "message": "No audio packets received."})
            await websocket.close()
            return
        if len(audio) % 2:
            audio.pop()

        started = asyncio.get_running_loop().time()
        wav_bytes = _pcm_to_wav(bytes(audio), 16000)
        transcript = await asyncio.to_thread(_transcribe_streamed_wav, wav_bytes)
        transcription_ms = (asyncio.get_running_loop().time() - started) * 1000
        await websocket.send_json({"type": "transcript", "text": transcript, "transcription_ms": round(transcription_ms, 1)})
        logger.info("Streaming voice transcript (%.0f ms): %s", transcription_ms, transcript)
        await _send_streamed_assistant_response(websocket, transcript, session_id)
    except WebSocketDisconnect:
        logger.info("Pi disconnected from voice WebSocket.")
    except Exception as error:
        logger.exception("Monday voice WebSocket failed.")
        try:
            await websocket.send_json({"type": "error", "message": str(error)[:300]})
            await websocket.close(code=1011)
        except Exception:
            pass

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(

        "server:app",

        host=os.getenv("MONDAY_BIND_HOST", "127.0.0.1"),

        port=8000,

        reload=False

    )

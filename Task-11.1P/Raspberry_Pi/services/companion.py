"""Polls the Monday Windows API for scheduled alarms and study-session check-ins."""
from __future__ import annotations

import logging
import math
import os
import random
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger("smart_desk_bot")

ROOT = Path(__file__).resolve().parents[1]

def _load_env_file():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

_load_env_file()

SERVER_URL = os.getenv("MONDAY_SERVER_URL", "http://monday-server:8000").rstrip("/")
WEB_URL = os.getenv(
    "MONDAY_WEB_URL",
    SERVER_URL.replace(":8000", ":8002")
).rstrip("/")
API_TOKEN = os.getenv("MONDAY_API_TOKEN", "")
POLL_SECONDS = max(3, int(os.getenv("MONDAY_DEVICE_POLL_SECONDS", "5")))
ALARM_REPEAT_SECONDS = max(30, int(os.getenv("MONDAY_ALARM_REPEAT_SECONDS", "90")))

MORNING_LINES = [
    "Good morning. A new day is a fresh chance to make progress.",
    "Good morning. Start gently, stay steady, and make today count.",
    "Good morning. You do not need a perfect day; just take the first step.",
    "Good morning. Keep your focus on what matters most today.",
    "Good morning. One small win at a time. You have got this.",
]

MOTIVATIONS = [
    "Quick focus check-in. You've been working steadily. Keep going, one clear step at a time.",
    "You're making progress even when it feels slow. Stay with the task; consistency wins.",
    "A little reminder from Monday: relax your shoulders, take a sip of water, and return to the next small step.",
    "Good work staying focused. Do not worry about the whole assignment right now. Just finish the next piece.",
    "You're further along than when you started. Keep your momentum, and remember to breathe.",
    "Focus check. If your concentration is fading, take a short stretch break, then come back refreshed.",
]

class DeviceCompanion:
    """Non-blocking device-side delivery for alarms and focus messages."""

    def __init__(self, assistant):
        self.assistant = assistant
        self.base_url = WEB_URL
        self.token = API_TOKEN
        self.running = False
        self.thread = None
        self._lock = threading.RLock()
        self._active_alarm = None
        self._last_alarm_spoken = 0.0
        self._session_id = None
        self._motivation_offsets = []
        self._motivation_next_index = 0
        self._last_network_log = 0.0

    def start(self):
        if self.running:
            return
        if not self.token:
            logger.error("Device companion not started: MONDAY_API_TOKEN is missing from the Pi .env file.")
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="MondayDeviceCompanion")
        self.thread.start()
        logger.info(
            "Monday device companion started. Web API: %s; duration-aware random study encouragement enabled.",
            self.base_url
        )

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        logger.info("Monday device companion stopped.")

    def _headers(self):
        return {"X-API-Key": self.token, "Content-Type": "application/json"}

    def _poll_server(self):
        response = requests.post(
            self.base_url + "/api/device/poll",
            headers=self._headers(),
            json={},
            timeout=8
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid Monday device-poll response.")
        return payload

    def _run(self):
        while self.running:
            try:
                payload = self._poll_server()
                self._last_network_log = 0.0
                self._handle_alarms(payload.get("due_alarms") or [])
                self._speak_pending_alarm()
                self._handle_study_session(payload.get("active_session"))
            except requests.RequestException as error:
                now = time.monotonic()
                if now - self._last_network_log >= 60:
                    logger.warning("Monday device companion cannot reach the Windows Web API at %s: %s", self.base_url, error)
                    self._last_network_log = now
            except Exception:
                logger.exception("Monday device companion polling failed.")
            time.sleep(POLL_SECONDS)

    def _handle_alarms(self, alarms):
        if not isinstance(alarms, list):
            return
        returned_ids = {
            int(item["history_id"]) for item in alarms
            if isinstance(item, dict) and item.get("history_id")
        }
        with self._lock:
            if self._active_alarm and int(self._active_alarm.get("history_id", -1)) not in returned_ids:
                self._active_alarm = None
                self._last_alarm_spoken = 0.0
        for alarm in alarms:
            if not isinstance(alarm, dict) or not alarm.get("history_id"):
                continue
            with self._lock:
                if self._active_alarm and self._active_alarm.get("history_id") == alarm.get("history_id"):
                    continue
                if self._active_alarm is None:
                    alarm = dict(alarm)
                    alarm["morning_line"] = random.choice(MORNING_LINES)
                    self._active_alarm = alarm
                    self._last_alarm_spoken = 0.0
                    logger.warning("Alarm due: %s (%s)", alarm.get("title"), alarm.get("scheduled_for"))
                    break

    def _speak_pending_alarm(self):
        with self._lock:
            alarm = self._active_alarm
            last_spoken = self._last_alarm_spoken
        if not alarm:
            return
        if self.assistant.speaking or self.assistant.awaiting_command:
            return
        now = time.monotonic()
        if last_spoken and now - last_spoken < ALARM_REPEAT_SECONDS:
            return
        title = str(alarm.get("title") or "Your alarm")
        topic = str(alarm.get("topic") or "").strip()
        morning_line = str(alarm.get("morning_line") or random.choice(MORNING_LINES))
        message = f"Alarm. {title}. It is time. {morning_line} Press the desk button to dismiss this alarm."
        if topic:
            message += f" Today's topic is {topic}."
        self.assistant.speak(message)
        with self._lock:
            if self._active_alarm and self._active_alarm.get("history_id") == alarm.get("history_id"):
                self._last_alarm_spoken = time.monotonic()

    @staticmethod
    def _make_motivation_schedule(session_id: int, duration_seconds: int) -> list[int]:
        """Build stable random offsets spread across this session's actual duration.

        Even a one-minute session gets one random check-in. Longer sessions get
        roughly one message per 30 minutes, capped to keep speech unobtrusive.
        A stable seed preserves the schedule if the Pi companion restarts.
        """
        duration_seconds = max(1, int(duration_seconds))
        count = min(12, max(1, math.ceil(duration_seconds / 1800)))
        start = duration_seconds * 0.18
        end = duration_seconds * 0.72
        if end <= start:
            return [max(1, int(duration_seconds * 0.5))]
        rng = random.Random(f"monday-study-{session_id}")
        width = (end - start) / count
        offsets = []
        for index in range(count):
            low = start + index * width
            high = start + (index + 1) * width
            offsets.append(max(1, min(duration_seconds - 1, int(rng.uniform(low, high)))))
        return sorted(set(offsets)) or [max(1, int(duration_seconds * 0.5))]

    def _handle_study_session(self, session):
        if not isinstance(session, dict) or not session.get("id"):
            with self._lock:
                self._session_id = None
                self._motivation_offsets = []
                self._motivation_next_index = 0
            return

        session_id = int(session["id"])
        try:
            duration_seconds = max(60, int(session.get("planned_minutes", 1)) * 60)
            started = datetime.fromisoformat(str(session["started_at"]))
            if started.tzinfo is None:
                started = started.astimezone()
            elapsed = max(0.0, (datetime.now().astimezone() - started).total_seconds())
        except (KeyError, TypeError, ValueError):
            logger.warning("Study session has invalid timing data; skipping motivation check-in.")
            return

        if elapsed >= duration_seconds:
            return
        with self._lock:
            if self._active_alarm is not None:
                return

        with self._lock:
            if session_id != self._session_id:
                self._session_id = session_id
                self._motivation_offsets = self._make_motivation_schedule(session_id, duration_seconds)
                self._motivation_next_index = 0
            offsets = list(self._motivation_offsets)
            next_index = self._motivation_next_index

        due_indices = [i for i in range(next_index, len(offsets)) if offsets[i] <= elapsed]
        if not due_indices:
            return
        due_index = due_indices[-1]
        if self.assistant.speaking or self.assistant.awaiting_command:
            return

        message = random.choice(MOTIVATIONS)
        task = str(session.get("task_title") or "your work")
        subject = str(session.get("subject_code") or "").strip()
        if subject:
            message = f"{message} You're working on {task} for {subject}."
        else:
            message = f"{message} You're working on {task}."
        logger.info(
            "Delivering random study motivation for session %s at %ss of %ss.",
            session_id, int(elapsed), duration_seconds
        )
        self.assistant.speak(message)
        with self._lock:
            if self._session_id == session_id:
                self._motivation_next_index = due_index + 1

    def on_button_pressed(self):
        """Called by NanoController; do network/audio work outside the serial thread."""
        with self._lock:
            alarm = self._active_alarm
        if not alarm:
            return
        threading.Thread(
            target=self._dismiss_alarm,
            args=(alarm,),
            daemon=True,
            name="MondayAlarmDismiss"
        ).start()

    def _dismiss_alarm(self, alarm):
        history_id = alarm.get("history_id")
        try:
            self.assistant.cancel_speech()
        except Exception:
            logger.exception("Could not silence alarm playback immediately.")
        try:
            response = requests.post(
                self.base_url + f"/api/device/alarms/{history_id}/dismiss",
                headers=self._headers(),
                json={},
                timeout=8
            )
            response.raise_for_status()
            with self._lock:
                if self._active_alarm and self._active_alarm.get("history_id") == history_id:
                    self._active_alarm = None
                    self._last_alarm_spoken = 0.0
            logger.info("Alarm %s dismissed with the physical button.", history_id)
            if not self.assistant.speaking:
                self.assistant.speak("Alarm dismissed. Good morning.")
        except Exception:
            logger.exception("Could not dismiss alarm %s on the Windows server.", history_id)

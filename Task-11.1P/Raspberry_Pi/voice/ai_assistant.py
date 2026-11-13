import io
import json
import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
from scipy.signal import resample_poly
from vosk import KaldiRecognizer

from voice.assistant import (
    VoiceAssistant as BaseVoiceAssistant,
    MIC_DEVICE,
    MIC_RATE,
    VOSK_RATE,
    WAKE_PATTERN,
    SPEAKER_DEVICE,
)

logger = logging.getLogger("smart_desk_bot")
ROOT = Path(__file__).resolve().parent.parent

def load_project_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'")
        )

load_project_env()

SERVER_URL = os.getenv(
    "MONDAY_SERVER_URL",
    "http://monday-server:8000"
).rstrip("/")

API_TOKEN = os.getenv("MONDAY_API_TOKEN", "")
STT_URL = os.getenv(
    "MONDAY_STT_URL",
    "http://monday-server:8001/v1/transcribe"
)
SESSION_ID = os.getenv(
    "MONDAY_SESSION_ID",
    "smart-desk-bot-main"
)

REQUEST_TIMEOUT = 100

SILENCE_END_SECONDS = 1.6
EMPTY_COMMAND_TIMEOUT = 12.0
MAX_COMMAND_SECONDS = 90.0
VOICE_RMS_THRESHOLD = 450

HARDWARE_TOOLS = {
    "set_light_brightness",
    "adjust_light_brightness",
    "turn_light_on",
    "turn_light_off",
    "set_light_auto",
    "get_hardware_status",
}

class MondayRemoteClient:

    def __init__(self):
        self.url = SERVER_URL + "/v1/assistant/message"
        self.token = API_TOKEN
        self.session_id = SESSION_ID

    def send_message(self, text):
        if not self.token:
            raise RuntimeError("Monday API token is missing.")

        payload = {
            "text": text,
            "session_id": self.session_id
        }

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.token
            },
            method="POST"
        )

        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT
        ) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not isinstance(result, dict):
            raise ValueError("Invalid Monday server response.")

        if not isinstance(result.get("response"), str):
            raise ValueError("Monday server returned no response.")

        return result

    def transcribe_audio(self, wav_bytes):
        if not self.token:
            raise RuntimeError("Monday API token is missing.")

        response = requests.post(
            STT_URL,
            headers={"X-API-Key": self.token},
            files={
                "audio": (
                    "utterance.wav",
                    wav_bytes,
                    "audio/wav"
                )
            },
            timeout=60
        )
        response.raise_for_status()

        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Invalid Whisper response.")

        text = result.get("text", "").strip()
        if not text:
            raise ValueError("Whisper returned an empty transcript.")

        return text

class VoiceAssistant(BaseVoiceAssistant):

    def __init__(self, nano):
        super().__init__(nano)

        self.ai_client = MondayRemoteClient()
        self._recording_lock = threading.Lock()
        self._recording_chunks = []
        self._recording_active = False
        self._recording_voice_seen = False

        self.interrupt_recognizer = None
        self.command_segments = []
        self.command_started_at = 0.0
        self.last_voice_at = 0.0

        self._worker_lock = threading.Lock()
        self._worker_thread = None

        self._playback_lock = threading.Lock()
        self._playback_process = None
        self._speech_cancel_event = None

        logger.info("Monday remote AI: %s", SERVER_URL)

    def audio_callback(self, indata, frames, timing, status):
        if not self.running:
            return

        if status:
            logger.debug("Audio input status: %s", status)

        samples = indata[:, 0]

        rms = float(np.sqrt(np.mean(
            samples.astype(np.float32) ** 2
        )))

        now = time.monotonic()

        if self.awaiting_command and rms >= VOICE_RMS_THRESHOLD:
            self.last_voice_at = now

        converted = resample_poly(
            samples.astype(np.float32),
            1,
            3
        )

        converted = np.clip(
            converted,
            -32768,
            32767
        ).astype(np.int16)

        if self.awaiting_command:
            with self._recording_lock:
                if self._recording_active:
                    self._recording_chunks.append(converted.tobytes())
                    if rms >= VOICE_RMS_THRESHOLD:
                        self._recording_voice_seen = True

        try:
            self.audio_queue.put_nowait(converted.tobytes())
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.put_nowait(converted.tobytes())
            except queue.Empty:
                pass

        if now - self.last_level_update >= 0.12:
            self.last_level_update = now
            self.display(level=min(1.0, rms / 12000.0))

    def _new_interrupt_recognizer(self):
        recognizer = KaldiRecognizer(self.model, VOSK_RATE)
        recognizer.SetWords(True)
        recognizer.SetMaxAlternatives(5)
        return recognizer

    def reset_recognizer(self):
        super().reset_recognizer()
        if self.model is not None:
            self.interrupt_recognizer = self._new_interrupt_recognizer()

    def _clear_audio_queue(self):
        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def _interrupt_speech(self):
        logger.info("Wake word detected during speech. Interrupting.")

        with self._playback_lock:
            event = self._speech_cancel_event
            process = self._playback_process

            if event is not None:
                event.set()

            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=0.5)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

            self._playback_process = None

        self.speaking = False
        self.command_segments = []
        self.command_started_at = time.monotonic()
        self.last_voice_at = time.monotonic()
        self.awaiting_command = True
        self.wake_detected_current = False
        self.command_deadline = (
            self.command_started_at + MAX_COMMAND_SECONDS
        )

        with self._recording_lock:
            self._recording_chunks = []
            self._recording_active = True
            self._recording_voice_seen = False

        self.activity.on()
        self.display(listening=True, speaking=False, level=0)

        self.reset_recognizer()

    def activate_listening(self):
        if self.awaiting_command:
            return

        self.awaiting_command = True
        self.wake_detected_current = True
        self.command_segments = []

        with self._recording_lock:
            self._recording_chunks = []
            self._recording_active = True
            self._recording_voice_seen = False

        now = time.monotonic()
        self.command_started_at = now
        self.last_voice_at = now
        self.command_deadline = now + MAX_COMMAND_SECONDS

        self.activity.on()
        self.display(listening=True, speaking=False)

        logger.info("Wake word detected. Listening for a complete request.")

    def _dispatch_accumulated_command(self):
        text = " ".join(self.command_segments).strip()

        with self._recording_lock:
            chunks = self._recording_chunks[:]
            voice_seen = self._recording_voice_seen
            self._recording_chunks = []
            self._recording_active = False
            self._recording_voice_seen = False

        self.awaiting_command = False
        self.wake_detected_current = False
        self.command_segments = []

        if not voice_seen or not chunks:
            self._finish_interaction()
            return

        audio = b"".join(chunks)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(VOSK_RATE)
            wav.writeframes(audio)

        wav_bytes = wav_buffer.getvalue()

        logger.info(
            "Captured %.2f seconds of audio for Whisper.",
            len(audio) / (VOSK_RATE * 2)
        )

        self._run_command(text or "__WHISPER_AUDIO__", wav_bytes)

    def _process_speaking_audio(self, audio):
        if self.interrupt_recognizer is None:
            self.interrupt_recognizer = self._new_interrupt_recognizer()

        try:
            if self.interrupt_recognizer.AcceptWaveform(audio):
                result = json.loads(
                    self.interrupt_recognizer.Result()
                )
                text = result.get("text", "").lower()
            else:
                result = json.loads(
                    self.interrupt_recognizer.PartialResult()
                )
                text = result.get("partial", "").lower()

            if WAKE_PATTERN.search(text):
                self._interrupt_speech()

        except Exception:
            logger.exception("Interrupt wake-word recognition failed.")

    def _process_idle_audio(self, audio):
        if self.recognizer is None:
            return

        if self.recognizer.AcceptWaveform(audio):
            text = self._extract_best_text(self.recognizer.Result())

            if not text:
                return

            logger.info("Recognised speech: %s", text)

            if self.wake_detected_current:
                command = self.detect_wake(text)
                self.wake_detected_current = False

                if command:
                    self.command_segments.append(command)
                    self.last_voice_at = time.monotonic()

                return

            if self.awaiting_command:
                self.command_segments.append(text)
                self.last_voice_at = time.monotonic()
                return

        else:
            partial = json.loads(
                self.recognizer.PartialResult()
            ).get("partial", "").strip().lower()

            if (
                partial
                and not self.awaiting_command
                and WAKE_PATTERN.search(partial)
            ):
                self.activate_listening()

    def listen_loop(self):
        logger.info("Monday continuous listening loop started.")

        while self.running:
            try:
                audio = self.audio_queue.get(timeout=0.10)
            except queue.Empty:
                audio = None

            if self.speaking:
                if audio is not None:
                    self._process_speaking_audio(audio)
                continue

            if audio is not None:
                self._process_idle_audio(audio)

            if not self.awaiting_command:
                continue

            now = time.monotonic()
            elapsed = now - self.command_started_at
            silence = now - self.last_voice_at

            if elapsed >= MAX_COMMAND_SECONDS:
                logger.info("Maximum utterance duration reached.")
                self._dispatch_accumulated_command()
                continue

            with self._recording_lock:
                voice_seen = self._recording_voice_seen

            if not voice_seen and silence >= EMPTY_COMMAND_TIMEOUT:
                logger.info("No speech received after wake word.")
                with self._recording_lock:
                    self._recording_active = False
                    self._recording_chunks = []
                self._finish_interaction()
                continue

            if voice_seen and silence >= SILENCE_END_SECONDS:
                logger.info("End of utterance detected.")
                self._dispatch_accumulated_command()

    def _run_command(self, text, audio_wav=None):
        if not text.strip():
            self._finish_interaction()
            return

        with self._worker_lock:
            if (
                self._worker_thread is not None
                and self._worker_thread.is_alive()
            ):
                pending = getattr(self, "_pending_commands", [])
                if len(pending) >= 3:
                    pending.pop(0)
                pending.append((text, audio_wav))
                self._pending_commands = pending
                logger.warning(
                    "Command queued because another command is still running: %s",
                    text
                )
                return

            self._pending_commands = getattr(self, "_pending_commands", [])
            self._start_command_worker_locked(text, audio_wav)

    def _start_command_worker_locked(self, text, audio_wav=None):
        worker = threading.Thread(
            target=self._command_worker,
            args=(text, audio_wav),
            daemon=True,
            name="MondayCommand"
        )
        self._worker_thread = worker
        worker.start()

    def _command_worker(self, text, audio_wav=None):
        try:
            if audio_wav is not None:
                logger.info("Sending captured speech to Windows Whisper.")
                transcript = self.ai_client.transcribe_audio(audio_wav)

                wake_command = self.detect_wake(transcript)
                if wake_command:
                    transcript = wake_command

                text = transcript.strip()
                if not text:
                    self.speak("I didn't catch that. Please try again.")
                    return

                logger.info("Whisper transcript: %s", text)

            logger.info("Processing complete request: %s", text)
            self.handle_command(text)

        except Exception:
            logger.exception("Monday command failed.")
            self.speak("Something went wrong while I was processing that.")

        finally:
            with self._worker_lock:
                pending = getattr(self, "_pending_commands", [])

                if pending:
                    next_command = pending.pop(0)
                    self._pending_commands = pending
                    if isinstance(next_command, tuple):
                        next_text, next_audio = next_command
                    else:
                        next_text, next_audio = next_command, None
                    logger.info("Starting queued command: %s", next_text)
                    self._start_command_worker_locked(next_text, next_audio)
                else:
                    self._worker_thread = None
                    if not self.speaking and not self.awaiting_command:
                        self._finish_interaction()

    def cancel_speech(self):
        """Stop the current speech playback immediately, if one is active."""
        with self._playback_lock:
            cancel_event = self._speech_cancel_event
            playback_process = self._playback_process
        if cancel_event is not None:
            cancel_event.set()
        if playback_process is not None and playback_process.poll() is None:
            try:
                playback_process.terminate()
            except Exception:
                logger.exception("Could not terminate current speech playback.")

    def speak(self, text):
        if not text:
            return

        logger.info("Monday says: %s", text)

        cancel_event = threading.Event()

        with self._playback_lock:
            self._speech_cancel_event = cancel_event
            self._playback_process = None

        self.speaking = True
        self.display(listening=False, speaking=True, level=0)

        wav_path = None

        try:
            fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

            self._synthesize_piper(text, wav_path)

            if cancel_event.is_set():
                return

            command = [
                "aplay", "-q", "-D", SPEAKER_DEVICE,
                "-c", "1", wav_path
            ]

            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                logger.exception("Piper playback could not start.")
                process = subprocess.Popen(
                    ["espeak-ng", "-v", "en-us", text],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )

            with self._playback_lock:
                self._playback_process = process

            while process.poll() is None:
                if cancel_event.wait(0.05):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=0.5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                    break

            if process.returncode not in (0, None) and not cancel_event.is_set():
                logger.warning("Speech playback exited with code %s", process.returncode)

        except Exception:
            logger.exception("Monday speech output failed.")

        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

            with self._playback_lock:
                if self._speech_cancel_event is cancel_event:
                    self._playback_process = None
                    self._speech_cancel_event = None
                    self.speaking = False

            if not self.awaiting_command:
                self.display(
                    listening=False,
                    speaking=False,
                    level=0
                )

            logger.info("Speech output finished.")

    @staticmethod
    def _valid_integer(value, minimum, maximum):
        return type(value) is int and minimum <= value <= maximum

    def _execute_device_action(self, tool, arguments):
        if not isinstance(arguments, dict):
            return False, "The hardware command was invalid."

        if not self.nano.connected:
            return False, "The desk controller is not connected."

        if tool == "set_light_brightness":
            percentage = arguments.get("percentage")

            if not self._valid_integer(percentage, 0, 100):
                return False, "The requested brightness was invalid."

            result = self._execute_and_confirm(
                f"LIGHT LEVEL {percentage}",
                timeout=1.5
            )

            if result is True:
                return True, f"Brightness set to {percentage} percent."

            return False, "I couldn't confirm the brightness change."

        if tool == "adjust_light_brightness":
            adjustment = arguments.get("adjustment")

            if (
                not self._valid_integer(adjustment, -100, 100)
                or adjustment == 0
            ):
                return False, "The brightness adjustment was invalid."

            result = self._execute_and_confirm(
                f"LIGHT ADJUST {adjustment}",
                timeout=1.5
            )

            if result is not True:
                return False, "I couldn't confirm the brightness adjustment."

            status = self.nano.request_status_and_wait(timeout=1.0)
            actual = status.get("brightness_percent") if status else None

            if actual is not None:
                return True, f"Brightness adjusted. It is now at {actual} percent."

            return True, "Brightness adjusted successfully."

        commands = {
            "turn_light_on": ("LIGHT ON", "The desk light is now on."),
            "turn_light_off": ("LIGHT OFF", "The desk light is now off."),
            "set_light_auto": ("LIGHT AUTO", "Automatic lighting is enabled.")
        }

        if tool in commands:
            command, message = commands[tool]
            result = self._execute_and_confirm(command, timeout=1.5)

            if result is True:
                return True, message

            return False, "The desk controller did not confirm the command."

        if tool == "get_hardware_status":
            status = self.nano.request_status_and_wait(timeout=1.5)

            if not status:
                return False, "I couldn't obtain a fresh hardware status."

            lux = status.get("lux")
            brightness = status.get("brightness_percent")
            mode = str(status.get("light_mode", "unknown"))

            lux_text = (
                "The light sensor reading is unavailable"
                if lux is None else f"The room is at {lux:.1f} lux"
            )
            brightness_text = (
                "Desk light brightness is unavailable"
                if brightness is None else f"The desk light is at {brightness} percent"
            )

            return True, f"{lux_text}. {brightness_text}. Mode: {mode}."

        return False, "That hardware operation is not permitted."

    def handle_command(self, text):
        parsed = self.command_parser.parse(text)

        logger.info(
            "Command analysis: original=%r normalized=%r intent=%s "
            "action=%s value=%s confidence=%.2f",
            text,
            parsed.normalized_text,
            parsed.intent,
            parsed.action,
            parsed.value,
            parsed.confidence,
        )

        if parsed.intent in {"time", "date", "status", "light"}:
            return super().handle_command(text)

        try:
            result = self.ai_client.send_message(text)

        except urllib.error.HTTPError as error:
            logger.error("Monday server HTTP error: %s", error.code)
            self.speak("I couldn't access my AI server.")
            return

        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
            RuntimeError,
        ) as error:
            logger.error("Monday server request failed: %s", error)
            self.speak("I can't reach my AI server right now.")
            return

        hardware_results = []

        for action in result.get("actions", []):
            if not isinstance(action, dict):
                continue

            tool = action.get("tool")
            if tool not in HARDWARE_TOOLS:
                continue

            success, message = self._execute_device_action(
                tool,
                action.get("arguments", {}),
            )
            hardware_results.append(message)

            logger.info(
                "Hardware action %s: %s",
                tool,
                "success" if success else "failure",
            )

        if hardware_results:
            self.speak(" ".join(hardware_results))
            return

        response = result.get("response", "").strip()
        self.speak(response or "I couldn't formulate a response.")

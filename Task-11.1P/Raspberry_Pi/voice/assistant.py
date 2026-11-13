import json
import logging
import math
import os
import queue
import random
import re
import socket
import subprocess
import tempfile
import threading
import time
import wave

from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

from scipy.signal import resample_poly
from vosk import Model, KaldiRecognizer

from voice.command_parser import SmartCommandParser
from hardware.activity_indicator import ActivityIndicator

logger = logging.getLogger("smart_desk_bot")

ROOT = Path(__file__).resolve().parent.parent

VOSK_MODEL = ROOT / "models/vosk-model-small-en-us-0.15"
PIPER_MODEL = ROOT / "models/piper/en_US-lessac-medium.onnx"

MIC_DEVICE = 1
MIC_RATE = 48000
VOSK_RATE = 16000

SPEAKER_DEVICE = "plughw:2,0"

DISPLAY_HOST = "127.0.0.1"
DISPLAY_PORT = 5055

WAKE_PATTERN = re.compile(r"\bmonday\b")

class VoiceAssistant:

    def __init__(self, nano):
        self.nano = nano
        self.command_parser = SmartCommandParser()
        self.activity = ActivityIndicator(pin=24)

        self.running = False
        self.speaking = False

        self.audio_queue = queue.Queue(maxsize=40)

        self.model = None
        self.recognizer = None

        self.stream = None
        self.thread = None

        self.awaiting_command = False
        self.wake_detected_current = False

        self.command_deadline = 0
        self.last_level_update = 0

        self.piper_voice = None
        self._speech_lock = threading.Lock()

        self.responses = {
            "unknown": [
                "Hmm, I didn't quite catch that. Try saying it a different way.",
                "I think I missed your point. Could you phrase that differently?",
                "That one slipped past me. Give me another try.",
                "I'm not completely sure what you meant there.",
                "I heard you, but I need a little more clarity.",
                "Let's try that once more. What would you like me to do?",
                "I might have misunderstood. Could you rephrase that?",
                "Sorry, I lost the meaning of that one.",
                "I'm listening, but I need a slightly clearer instruction.",
                "That didn't quite make sense to me. Let's try again.",
                "I caught some of that, but not enough to act confidently.",
                "Could you say that another way? I don't want to guess.",
            ],

            "light_down": [
                "Got it. I'll soften the lighting.",
                "Of course. Let's make it a little easier on the eyes.",
                "Understood. Turning the brightness down.",
                "I'll ease the brightness back.",
                "A little less glare coming right up.",
                "Let's bring that brightness down.",
                "On it. I'll make the light more comfortable.",
                "Fair enough. I'll tone it down.",
            ],

            "light_up": [
                "Got it. I'll bring up the brightness.",
                "Sure. Let's make the desk a little brighter.",
                "On it. Adding a little more light.",
                "I'll brighten things up.",
                "Absolutely. Let's make it easier to see.",
                "A little more illumination coming right up.",
                "Understood. I'll raise the brightness.",
                "Let's give you a bit more light.",
            ],

            "light_on": [
                "There we go. The light is on.",
                "Lighting is on.",
                "All lit up.",
                "Done. I've switched the light on.",
                "Your desk light is on.",
            ],

            "light_off": [
                "Lights out.",
                "Done. I've switched the light off.",
                "The desk light is now off.",
                "Turning the illumination off.",
                "All switched off.",
            ],

            "light_auto": [
                "Automatic lighting is back on.",
                "I've handed brightness control back to the light sensor.",
                "Adaptive lighting restored.",
                "The BH1750 is back in control.",
                "Automatic brightness is enabled again.",
            ],

            "failure": [
                "I sent the request, but the Arduino did not confirm it.",
                "Something went wrong on the hardware side.",
                "The light controller did not acknowledge that command.",
                "I couldn't verify that the change was applied.",
            ],

            "uncertain": [
                "I sent the request, but I haven't received confirmation yet.",
                "The command was transmitted, but the hardware response is missing.",
                "I'm not certain the change was applied. The Arduino hasn't replied.",
            ],
        }

    def display(self, **message):
        try:
            payload = json.dumps(message).encode("utf-8")

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(
                    payload,
                    (DISPLAY_HOST, DISPLAY_PORT),
                )

        except OSError:
            logger.debug("Display UDP message could not be sent.")

    def _finish_interaction(self):
        self.awaiting_command = False
        self.wake_detected_current = False

        self.activity.off()

        self.display(
            listening=False,
            speaking=False,
            level=0,
        )

    def reset_recognizer(self):
        if self.model is not None:
            self.recognizer = KaldiRecognizer(
                self.model,
                VOSK_RATE,
            )

            self.recognizer.SetWords(True)
            self.recognizer.SetMaxAlternatives(5)

        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def _load_piper(self):
        try:
            from piper import PiperVoice

            self.piper_voice = PiperVoice.load(
                str(PIPER_MODEL)
            )

            logger.info("Piper voice loaded and kept in memory.")

        except Exception:
            self.piper_voice = None

            logger.exception(
                "Persistent Piper loading failed. "
                "CLI fallback will be used."
            )

    def start(self):
        if self.running:
            return

        if not VOSK_MODEL.exists():
            raise FileNotFoundError(
                f"Vosk model missing: {VOSK_MODEL}"
            )

        if not PIPER_MODEL.exists():
            raise FileNotFoundError(
                f"Piper model missing: {PIPER_MODEL}"
            )

        logger.info("Loading Vosk speech model...")

        self.model = Model(str(VOSK_MODEL))
        self.reset_recognizer()
        self._load_piper()

        device = sd.query_devices(
            MIC_DEVICE,
            "input",
        )

        logger.info(
            "Microphone: %s at %s Hz mono",
            device["name"],
            MIC_RATE,
        )

        self.running = True

        self.stream = sd.InputStream(
            device=MIC_DEVICE,
            samplerate=MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=4800,
            callback=self.audio_callback,
        )

        self.stream.start()

        self.thread = threading.Thread(
            target=self.listen_loop,
            daemon=True,
            name="VoiceAssistant",
        )

        self.thread.start()

        logger.info("Voice assistant ready. Wake word: monday.")
        self.speak("monday is ready.")

    def audio_callback(self, indata, frames, timing, status):
        if not self.running or self.speaking:
            return

        if status:
            logger.debug("Audio input status: %s", status)

        samples = indata[:, 0]

        converted = resample_poly(
            samples.astype(np.float32),
            1,
            3,
        )

        converted = np.clip(
            converted,
            -32768,
            32767,
        ).astype(np.int16)

        try:
            self.audio_queue.put_nowait(
                converted.tobytes()
            )

        except queue.Full:
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.put_nowait(
                    converted.tobytes()
                )
            except queue.Empty:
                pass

        now = time.monotonic()

        if now - self.last_level_update >= 0.12:
            self.last_level_update = now

            rms = math.sqrt(
                float(
                    np.mean(
                        samples.astype(np.float32) ** 2
                    )
                )
            )

            self.display(
                level=min(1.0, rms / 12000.0)
            )

    @staticmethod
    def detect_wake(text):
        match = WAKE_PATTERN.search(text)

        if not match:
            return None

        return text[match.end():].strip(" ,.!?")

    def activate_listening(self):
        if self.awaiting_command:
            return

        self.awaiting_command = True
        self.wake_detected_current = True
        self.command_deadline = time.monotonic() + 8.0

        self.activity.on()

        self.display(
            listening=True,
            speaking=False,
        )

        logger.info("Wake word detected. Listening.")

    @staticmethod
    def _extract_best_text(result_json):
        try:
            payload = json.loads(result_json)
        except json.JSONDecodeError:
            return ""

        alternatives = payload.get("alternatives")

        if alternatives:
            best = max(
                alternatives,
                key=lambda item: float(
                    item.get("confidence", 0.0)
                ),
            )

            return best.get("text", "").strip().lower()

        return payload.get("text", "").strip().lower()

    def listen_loop(self):
        logger.info("Listening continuously for monday.")

        while self.running:

            if (
                self.awaiting_command
                and time.monotonic() > self.command_deadline
            ):
                logger.info("Command listening timed out.")
                self._finish_interaction()
                continue

            try:
                audio = self.audio_queue.get(
                    timeout=0.15
                )

            except queue.Empty:
                continue

            if self.speaking or self.recognizer is None:
                continue

            try:
                if self.recognizer.AcceptWaveform(audio):

                    text = self._extract_best_text(
                        self.recognizer.Result()
                    )

                    if text:
                        logger.info(
                            "Recognised speech: %s",
                            text,
                        )

                        self.handle_final_result(text)

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

            except Exception:
                logger.exception(
                    "Speech recognition processing failed."
                )

    def handle_final_result(self, text):

        if self.wake_detected_current:
            self.wake_detected_current = False

            command = self.detect_wake(text)

            if command:
                self._run_command(command)

            else:
                self.awaiting_command = True
                self.command_deadline = (
                    time.monotonic() + 8.0
                )

            return

        if self.awaiting_command:
            self._run_command(text)
            return

        command = self.detect_wake(text)

        if command is not None:
            self.activate_listening()

            if command:
                self._run_command(command)

    def _run_command(self, text):
        self.awaiting_command = False
        self.wake_detected_current = False

        try:
            self.handle_command(text)

        except Exception:
            logger.exception("Command execution failed.")

            self.speak(
                "Something unexpected happened while I was processing that."
            )

        finally:
            self._finish_interaction()

    def _synthesize_piper(self, text, wav_path):

        if self.piper_voice is not None:
            with wave.open(wav_path, "wb") as wav_file:
                self.piper_voice.synthesize_wav(
                    text,
                    wav_file,
                )

            return

        subprocess.run(
            [
                "piper",
                "--model",
                str(PIPER_MODEL),
                "--output_file",
                wav_path,
            ],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=30,
        )

    def speak(self, text):
        logger.info("monday says: %s", text)

        with self._speech_lock:
            self.speaking = True

            self.display(
                listening=False,
                speaking=True,
            )

            wav_path = None

            try:
                fd, wav_path = tempfile.mkstemp(
                    suffix=".wav"
                )

                os.close(fd)

                try:
                    self._synthesize_piper(
                        text,
                        wav_path,
                    )

                    subprocess.run(
                        [
                            "aplay",
                            "-q",
                            "-D",
                            SPEAKER_DEVICE,
                            "-c",
                            "1",
                            wav_path,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        check=True,
                        timeout=30,
                    )

                except Exception:
                    logger.exception(
                        "Piper playback failed. Trying eSpeak."
                    )

                    subprocess.run(
                        [
                            "espeak-ng",
                            "-v",
                            "en-us",
                            text,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=20,
                        check=False,
                    )

            except Exception:
                logger.exception("Speech output failed.")

            finally:
                if wav_path and os.path.exists(wav_path):
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass

                self.speaking = False

                self.reset_recognizer()

                self.display(
                    speaking=False,
                    listening=False,
                    level=0,
                )

                logger.info("Ready for the next wake word.")

    def _random_response(self, category):
        options = self.responses.get(category, [])

        if not options:
            return "All right."

        return random.choice(options)

    def _execute_and_confirm(self, command, timeout=1.2):
        if not self.nano.connected:
            return False

        return self.nano.execute_command(
            command,
            timeout=timeout,
        )

    def _handle_light_command(self, parsed):

        if parsed.action == "adjust":
            adjustment = int(parsed.value)

            confirmed = self._execute_and_confirm(
                f"LIGHT ADJUST {adjustment}",
                timeout=1.2,
            )

            if confirmed is False:
                self.speak(self._random_response("failure"))
                return

            if confirmed is None:
                self.speak(self._random_response("uncertain"))
                return

            status = self.nano.request_status_and_wait(
                timeout=0.8
            )

            actual = (
                status.get("brightness_percent")
                if status else None
            )

            category = (
                "light_down" if adjustment < 0 else "light_up"
            )

            response = self._random_response(category)

            if actual is not None:
                response += f" It is now at {actual} percent."

            self.speak(response)
            return

        if parsed.action == "level":
            percentage = int(parsed.value)

            confirmed = self._execute_and_confirm(
                f"LIGHT LEVEL {percentage}"
            )

            if confirmed is True:
                self.speak(
                    f"Brightness set to {percentage} percent."
                )
            elif confirmed is False:
                self.speak(self._random_response("failure"))
            else:
                self.speak(self._random_response("uncertain"))

            return

        commands = {
            "on": ("LIGHT ON", "light_on"),
            "off": ("LIGHT OFF", "light_off"),
            "auto": ("LIGHT AUTO", "light_auto"),
        }

        if parsed.action not in commands:
            self.speak(self._random_response("unknown"))
            return

        command, response_category = commands[parsed.action]

        confirmed = self._execute_and_confirm(command)

        if confirmed is True:
            self.speak(
                self._random_response(response_category)
            )

        elif confirmed is False:
            self.speak(self._random_response("failure"))

        else:
            self.speak(self._random_response("uncertain"))

    def handle_command(self, text):
        parsed = self.command_parser.parse(text)

        logger.info(
            "Command analysis: original=%r normalized=%r "
            "intent=%s action=%s value=%s confidence=%.2f",
            text,
            parsed.normalized_text,
            parsed.intent,
            parsed.action,
            parsed.value,
            parsed.confidence,
        )

        if parsed.intent == "time":
            current_time = datetime.now().strftime(
                "%I:%M %p"
            )

            self.speak(
                random.choice([
                    f"It's {current_time}.",
                    f"The current time is {current_time}.",
                    f"Right now, it's {current_time}.",
                    f"Your clock says {current_time}.",
                ])
            )

            return

        if parsed.intent == "date":
            current_date = datetime.now().strftime(
                "%A, %d %B %Y"
            )

            self.speak(
                random.choice([
                    f"Today is {current_date}.",
                    f"It's {current_date} today.",
                    f"The date today is {current_date}.",
                ])
            )

            return

        if parsed.intent == "status":
            status = self.nano.request_status_and_wait(
                timeout=1.0
            )

            if not status:
                self.speak(
                    "I haven't received a fresh hardware status report."
                )
                return

            lux = status.get("lux")
            brightness = status.get("brightness_percent")
            mode = status.get("light_mode", "unknown")

            lux_text = (
                "The light sensor is currently unavailable."
                if lux is None
                else f"The room is at {lux:.1f} lux."
            )

            brightness_text = (
                "Brightness information is unavailable."
                if brightness is None
                else f"The desk light is at {brightness} percent."
            )

            mode_text = mode.replace("_", " ").lower()

            self.speak(
                f"{lux_text} {brightness_text} "
                f"Lighting mode is {mode_text}."
            )

            return

        if parsed.intent == "light":
            self._handle_light_command(parsed)
            return

        self.speak(self._random_response("unknown"))

    def stop(self):
        logger.info("Stopping voice assistant...")

        self.running = False
        self._finish_interaction()

        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                logger.exception("Error stopping microphone stream.")

            self.stream = None

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

        self.activity.close()

        logger.info("Voice assistant stopped.")

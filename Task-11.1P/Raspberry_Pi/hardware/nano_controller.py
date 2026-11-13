import logging
import threading
import time
from collections import deque

import serial
from serial import SerialException

logger = logging.getLogger("smart_desk_bot")

class NanoController:
    def __init__(
        self,
        port="/dev/ttyACM0",
        baudrate=115200,
        reconnect_interval=3,
        on_status=None,
        on_button=None,
        on_event=None,
    ):
        self.port = port
        self.baudrate = baudrate
        self.reconnect_interval = reconnect_interval

        self.on_status = on_status
        self.on_button = on_button
        self.on_event = on_event

        self.connection = None
        self.connected = False

        self._running = False
        self._thread = None

        self._lock = threading.RLock()
        self._condition = threading.Condition()

        self._pending_commands = deque()
        self._ack_results = {}
        self._status_version = 0

        self.last_message = None
        self.last_status = None
        self.last_seen = None

    def start(self):
        if self._running:
            return

        self._running = True

        self._thread = threading.Thread(
            target=self._communication_loop,
            daemon=True,
            name="NanoController",
        )

        self._thread.start()
        logger.info("Nano controller started.")

    def stop(self):
        self._running = False

        with self._condition:
            while self._pending_commands:
                command = self._pending_commands.popleft()
                self._ack_results[command] = False

            self._condition.notify_all()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

        self._disconnect()
        logger.info("Nano controller stopped.")

    def _connect(self):
        try:
            connection = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=0.15,
                write_timeout=0.5,
            )

            time.sleep(2)

            with self._lock:
                self.connection = connection
                self.connected = True

            logger.info("Connected to Nano at %s", self.port)
            return True

        except (SerialException, OSError):
            logger.exception("Nano connection failed.")
            self._disconnect()
            return False

    def _disconnect(self):
        with self._lock:
            connection = self.connection
            self.connection = None
            self.connected = False

        if connection is not None:
            try:
                if connection.is_open:
                    connection.close()
            except (SerialException, OSError):
                logger.exception("Error closing Nano connection.")

        with self._condition:
            while self._pending_commands:
                command = self._pending_commands.popleft()
                self._ack_results[command] = False

            self._condition.notify_all()

    def send_command(self, command):
        if not isinstance(command, str) or not command.strip():
            return False

        command = command.strip().upper()

        if "\n" in command or "\r" in command:
            logger.warning("Rejected invalid serial command.")
            return False

        tracked = command not in {"PING", "STATUS"}

        with self._lock:
            if not self.connected or self.connection is None:
                logger.warning("Nano is disconnected.")
                return False

            try:
                if tracked:
                    with self._condition:
                        self._pending_commands.append(command)

                self.connection.write(
                    (command + "\n").encode("utf-8")
                )
                self.connection.flush()

                logger.info("Sent to Nano: %s", command)
                return True

            except (SerialException, OSError):
                if tracked:
                    with self._condition:
                        try:
                            self._pending_commands.remove(command)
                        except ValueError:
                            pass

                logger.exception("Command transmission failed.")
                self.connected = False
                return False

    def wait_for_ack(self, command, timeout=1.2):
        command = command.strip().upper()
        deadline = time.monotonic() + timeout

        with self._condition:
            while command not in self._ack_results:
                if not self._running:
                    return None

                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    return None

                self._condition.wait(remaining)

            return self._ack_results.pop(command)

    def execute_command(self, command, timeout=1.2):
        if not self.send_command(command):
            return False

        return self.wait_for_ack(command, timeout)

    def light_on(self):
        return self.send_command("LIGHT ON")

    def light_off(self):
        return self.send_command("LIGHT OFF")

    def light_auto(self):
        return self.send_command("LIGHT AUTO")

    def light_level(self, percentage):
        if type(percentage) is not int or not 0 <= percentage <= 100:
            logger.warning("Invalid brightness percentage: %s", percentage)
            return False

        return self.send_command(f"LIGHT LEVEL {percentage}")

    def light_adjust(self, percentage_points):
        if type(percentage_points) is not int:
            return False

        if not -100 <= percentage_points <= 100:
            return False

        return self.send_command(
            f"LIGHT ADJUST {percentage_points}"
        )

    def request_status(self):
        return self.send_command("STATUS")

    def request_status_and_wait(self, timeout=1.0):
        with self._condition:
            previous_version = self._status_version

        if not self.request_status():
            return self.get_status()

        deadline = time.monotonic() + timeout

        with self._condition:
            while self._status_version <= previous_version:
                if not self._running:
                    break

                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    return None

                self._condition.wait(remaining)

        return self.get_status()

    def get_status(self):
        with self._condition:
            return (
                self.last_status.copy()
                if self.last_status is not None
                else None
            )

    def _take_pending(self, predicate):
        with self._condition:
            for command in list(self._pending_commands):
                if predicate(command):
                    self._pending_commands.remove(command)
                    return command

        return None

    def _match_pending_ack(self, message):
        parts = message.split(",")

        if len(parts) < 3 or parts[0] != "OK":
            return None

        if parts[1] == "LIGHT":

            if parts[2] == "AUTO":
                return self._take_pending(
                    lambda c: c == "LIGHT AUTO"
                )

            if parts[2] == "LEVEL":
                return self._take_pending(
                    lambda c: c.startswith("LIGHT ")
                    and c != "LIGHT AUTO"
                )

            if parts[2] in {"ON", "OFF"}:
                return self._take_pending(
                    lambda c: c == f"LIGHT {parts[2]}"
                )

        return None

    def _match_error(self, message):
        message = message.upper()

        if "LIGHT" in message:
            return self._take_pending(
                lambda c: c.startswith("LIGHT ")
            )

        if "UNKNOWN_COMMAND" in message or "COMMAND_TOO_LONG" in message:
            with self._condition:
                if self._pending_commands:
                    return self._pending_commands.popleft()

        return None

    def _handle_message(self, message):
        self.last_message = message
        self.last_seen = time.time()

        logger.info("Nano: %s", message)

        acknowledged = self._match_pending_ack(message)

        if acknowledged:
            with self._condition:
                self._ack_results[acknowledged] = True
                self._condition.notify_all()

        elif message.startswith("ERR,"):
            logger.error("Nano reported error: %s", message)

            failed = self._match_error(message)

            if failed:
                with self._condition:
                    self._ack_results[failed] = False
                    self._condition.notify_all()

        if message.startswith("DATA,"):
            values = message.split(",")

            if len(values) >= 7:
                try:
                    lux = (
                        float(values[1])
                        if values[1] != "SENSOR_ERROR"
                        else None
                    )

                    brightness = None

                    if len(values) >= 8:
                        try:
                            brightness = int(values[7])
                        except ValueError:
                            brightness = None

                    status = {
                        "lux": lux,
                        "light": values[2],
                        "light_mode": values[3],
                        "environment": values[4],
                        "brightness_percent": brightness,
                        "received_at": self.last_seen,
                    }

                    with self._condition:
                        self.last_status = status
                        self._status_version += 1
                        self._condition.notify_all()

                    logger.info("Hardware status updated: %s", status)

                    if self.on_status:
                        try:
                            self.on_status(status.copy())
                        except Exception:
                            logger.exception("Status callback failed.")

                except (ValueError, IndexError):
                    logger.exception("Invalid Nano status message.")

        elif message.startswith("EVENT,BUTTON_PRESSED"):
            logger.info("Physical button pressed.")

            if self.on_button:
                try:
                    self.on_button()
                except Exception:
                    logger.exception("Button callback failed.")

        elif message.startswith("EVENT,"):
            logger.warning("Hardware event: %s", message)

            if self.on_event:
                try:
                    self.on_event(message)
                except Exception:
                    logger.exception("Hardware event callback failed.")

    def _communication_loop(self):
        last_ping = 0.0

        while self._running:

            if not self.connected:
                if not self._connect():
                    time.sleep(self.reconnect_interval)
                    continue

                last_ping = 0.0

            try:
                connection = self.connection

                if connection is None:
                    continue

                if connection.in_waiting:
                    raw = connection.readline()

                    if raw:
                        message = raw.decode(
                            "utf-8",
                            errors="replace",
                        ).strip()

                        if message:
                            self._handle_message(message)

                now = time.monotonic()

                if now - last_ping >= 1.0:
                    if self.send_command("PING"):
                        last_ping = now

                time.sleep(0.01)

            except (SerialException, OSError):
                logger.exception("Nano communication interrupted.")
                self._disconnect()
                time.sleep(self.reconnect_interval)

        self._disconnect()

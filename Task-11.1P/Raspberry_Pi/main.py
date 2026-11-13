import logging
import signal
import subprocess
import sys
import time

from config.settings import APP_NAME, DATA_DIR
from core.logging_setup import setup_logging
from hardware.nano_controller import NanoController
from voice.ai_assistant import VoiceAssistant
from services.companion import DeviceCompanion

def main():
    logger = setup_logging()

    logger.info("%s starting...", APP_NAME)
    logger.info("Project data directory: %s", DATA_DIR)

    nano = NanoController()
    assistant = VoiceAssistant(nano)
    companion = DeviceCompanion(assistant)
    nano.on_button = companion.on_button_pressed

    display_process = None

    try:
        logger.info("Starting Arduino Nano communication...")
        nano.start()

        logger.info("Starting display interface...")

        display_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "display.run_ui",
            ]
        )

        logger.info(
            "Display process started. PID: %s",
            display_process.pid,
        )

        logger.info("Starting Monday AI voice assistant...")

        try:
            assistant.start()

            logger.info(
                "Voice assistant started successfully."
            )
            companion.start()

        except Exception:
            logger.exception(
                "Voice assistant could not start. "
                "Display and hardware services will remain running."
            )

        logger.info("%s is running.", APP_NAME)

        while True:

            if (
                display_process is not None
                and display_process.poll() is not None
            ):
                logger.error(
                    "Display process stopped unexpectedly."
                )

                display_process = None

            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutdown requested by user.")

    except Exception:
        logger.exception("Unexpected application error.")

    finally:
        logger.info("Stopping Monday device companion...")
        companion.stop()
        logger.info("Stopping voice assistant...")
        assistant.stop()

        if (
            display_process is not None
            and display_process.poll() is None
        ):
            logger.info("Stopping display...")

            display_process.send_signal(signal.SIGINT)

            try:
                display_process.wait(timeout=5)

            except subprocess.TimeoutExpired:
                display_process.terminate()
                display_process.wait(timeout=2)

        logger.info("Stopping hardware controller...")
        nano.stop()

        logger.info("%s shut down.", APP_NAME)

if __name__ == "__main__":
    main()

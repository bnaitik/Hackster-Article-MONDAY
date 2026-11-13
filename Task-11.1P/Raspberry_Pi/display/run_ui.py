import json
import logging
import socket
import threading

from display.ui import DeskBotUI

logger = logging.getLogger("smart_desk_bot")

def display_receiver(interface):
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 5055))
    server.settimeout(1)

    try:
        while True:
            try:
                data, _ = server.recvfrom(1024)
            except socket.timeout:
                continue

            try:
                message = json.loads(data.decode("utf-8"))

                if "listening" in message:
                    interface.set_listening(message["listening"])

                if "speaking" in message:
                    interface.set_speaking(message["speaking"])

                if "level" in message:
                    interface.set_voice_level(message["level"])

            except (ValueError, TypeError, KeyError):
                logger.exception("Invalid display voice message.")

    finally:
        server.close()

def main():
    interface = None

    try:
        interface = DeskBotUI()

        receiver = threading.Thread(
            target=display_receiver,
            args=(interface,),
            daemon=True,
            name="DisplayVoiceReceiver",
        )
        receiver.start()

        interface.run()

    except Exception:
        import traceback
        traceback.print_exc()

        if interface is not None:
            try:
                interface.lcd.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()

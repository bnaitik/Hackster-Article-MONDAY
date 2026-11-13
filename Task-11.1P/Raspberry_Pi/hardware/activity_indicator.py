import logging

logger = logging.getLogger("smart_desk_bot")

class ActivityIndicator:
    """On/off activity indicator driven through a MOSFET."""

    def __init__(self, pin=24):
        self.pin = pin
        self.led = None
        self.available = False

        try:
            from gpiozero import LED

            self.led = LED(
                self.pin,
                active_high=True,
                initial_value=False,
            )

            self.available = True
            logger.info(
                "Activity indicator ready on BCM GPIO %s.",
                self.pin,
            )

        except Exception:
            logger.exception(
                "Activity indicator unavailable. "
                "The voice assistant will continue without it."
            )

    def on(self):
        if not self.available or self.led is None:
            return

        try:
            self.led.on()
        except Exception:
            logger.exception("Could not turn activity indicator on.")

    def off(self):
        if not self.available or self.led is None:
            return

        try:
            self.led.off()
        except Exception:
            logger.exception("Could not turn activity indicator off.")

    def close(self):
        if self.led is None:
            return

        try:
            self.led.off()
            self.led.close()
        except Exception:
            logger.exception("Error closing activity indicator.")

        self.led = None
        self.available = False

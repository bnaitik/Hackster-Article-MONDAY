import re
import time
from dataclasses import dataclass

@dataclass
class ParsedCommand:
    intent: str
    action: str = ""
    value: str = ""
    original_text: str = ""
    normalized_text: str = ""
    confidence: float = 1.0

class SmartCommandParser:
    """
    Offline natural-language command interpreter for monday.

    Lighting adjustments are expressed as percentage-point changes.
    They preserve automatic BH1750 mode through LIGHT ADJUST commands.
    """

    LIGHT_WORDS = {
        "light", "lights", "lighting", "led", "lamp", "lamps",
        "illumination", "brightness",
    }

    def __init__(self):
        self.last_light_context = 0.0
        self.context_duration = 90.0

    def normalize(self, speech):
        text = str(speech or "").lower().strip()

        replacements = {
            "what's": "what is",
            "what's": "what is",
            "it's": "it is",
            "its": "it is",
            "that's": "that is",
            "that's": "that is",
            "can't": "cannot",
            "couldn't": "could not",
            "isn't": "is not",
            "aren't": "are not",
            "doesn't": "does not",
            "don't": "do not",
            "i'm": "i am",
            "we're": "we are",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        text = re.sub(r"(.)\1{2,}", r"\1\1", text)

        text = re.sub(r"[^a-z0-9\s%]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        lighting_context = (
            re.search(
                r"\b(light|lights|led|lamp|brightness|turn|switch|"
                r"on|off|illuminate)\b",
                text,
            )
            is not None
        )

        if lighting_context:
            text = re.sub(
                r"\b(like|lite|late|layt)\b",
                "light",
                text,
            )

        text = re.sub(r"\b(hey\s+)?monday\b", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return text

    @staticmethod
    def _has(text, phrases):
        for phrase in phrases:
            if re.search(r"\b" + re.escape(phrase) + r"\b", text):
                return True
        return False

    def _light_command(self, action, value, original, normalized, confidence):
        self.last_light_context = time.monotonic()

        return ParsedCommand(
            intent="light",
            action=action,
            value=str(value),
            original_text=original,
            normalized_text=normalized,
            confidence=confidence,
        )

    def _has_recent_light_context(self):
        return (
            time.monotonic() - self.last_light_context
            <= self.context_duration
        )

    def parse(self, speech):
        original = str(speech or "")
        text = self.normalize(original)

        if not text:
            return ParsedCommand(
                "unknown",
                original_text=original,
                normalized_text=text,
                confidence=0.0,
            )

        recent_light_context = self._has_recent_light_context()

        if self._has(text, [
            "time", "clock", "current time", "what time",
        ]):
            return ParsedCommand(
                "time",
                original_text=original,
                normalized_text=text,
                confidence=0.98,
            )

        if self._has(text, [
            "date", "today", "tomorrow", "yesterday",
            "weekday", "what day",
        ]):
            return ParsedCommand(
                "date",
                original_text=original,
                normalized_text=text,
                confidence=0.98,
            )

        status_phrases = [
            "status",
            "hardware status",
            "check the hardware",
            "check the system",
            "health check",
            "how bright is it",
            "what is the brightness",
            "what is the light level",
            "how much light",
            "how dark is it",
            "is the light working",
        ]

        if self._has(text, status_phrases):
            return ParsedCommand(
                "status",
                original_text=original,
                normalized_text=text,
                confidence=0.96,
            )

        if self._has(text, [
            "automatic",
            "automatically",
            "auto mode",
            "sensor mode",
            "adaptive mode",
            "automatic mode",
        ]):
            return self._light_command(
                "auto", "", original, text, 0.99
            )

        percentage_match = re.search(
            r"\b(?:to|at|on|level)\s*(\d{1,3})\s*(?:percent|%)?",
            text,
        )

        if percentage_match and self._has(text, [
            "brightness", "light", "lights", "led",
            "lamp", "set", "make",
        ]):
            percentage = int(percentage_match.group(1))

            if 0 <= percentage <= 100:
                return self._light_command(
                    "level",
                    percentage,
                    original,
                    text,
                    0.98,
                )

        has_light_reference = (
            self._has(text, self.LIGHT_WORDS)
            or recent_light_context
        )

        mild_words = [
            "slightly", "a little", "a bit",
            "just a touch", "somewhat",
        ]

        strong_words = [
            "extremely", "way too", "really",
            "incredibly", "very much",
        ]

        mild = self._has(text, mild_words)
        strong = self._has(text, strong_words)

        negated_brightness = re.search(
            r"\b(not|never|no longer)\s+(?:too\s+)?"
            r"(bright|dim|dark)\b",
            text,
        )

        if not negated_brightness:

            too_bright = any(phrase in text for phrase in [
                "too bright",
                "way too bright",
                "too much light",
                "blinding",
                "it hurts my eyes",
                "hurting my eyes",
                "the light is harsh",
                "brightness is too high",
                "far too bright",
                "light is overwhelming",
                "glare is too much",
                "there is too much light",
            ])

            too_dim = any(phrase in text for phrase in [
                "too dim",
                "too dark",
                "far too dark",
                "cannot see",
                "cant see",
                "can barely see",
                "hard to see",
                "not enough light",
                "need more light",
                "room is gloomy",
                "it is gloomy",
                "brightness is too low",
            ])

            if too_bright and not too_dim:
                adjustment = -20 if mild else -50

                if strong:
                    adjustment = -50

                return self._light_command(
                    "adjust",
                    adjustment,
                    original,
                    text,
                    0.98,
                )

            if too_dim and not too_bright:
                adjustment = 10 if mild else 25

                if strong:
                    adjustment = 35

                return self._light_command(
                    "adjust",
                    adjustment,
                    original,
                    text,
                    0.97,
                )

            increase_phrases = [
                "make it brighter",
                "make this brighter",
                "brighten it",
                "brighten up",
                "brighten the desk",
                "more light",
                "increase brightness",
                "turn the brightness up",
                "turn it up",
                "light it up",
                "i need more light",
                "it is a little dark",
                "getting dark",
                "it is dark",
                "make the room brighter",
                "raise the brightness",
            ]

            if any(p in text for p in increase_phrases):
                adjustment = 10 if mild else 35 if strong else 25

                return self._light_command(
                    "adjust",
                    adjustment,
                    original,
                    text,
                    0.94,
                )

            decrease_phrases = [
                "make it dimmer",
                "dim it",
                "dim the light",
                "dim the lamp",
                "make it darker",
                "less light",
                "reduce brightness",
                "lower the brightness",
                "turn the brightness down",
                "turn it down",
                "tone it down",
                "soften the light",
                "the light is harsh",
                "it is glaring",
                "there is a glare",
            ]

            if any(p in text for p in decrease_phrases):
                adjustment = -10 if mild else -35 if strong else -25

                return self._light_command(
                    "adjust",
                    adjustment,
                    original,
                    text,
                    0.94,
                )

            if recent_light_context:
                if self._has(text, [
                    "more", "brighter", "higher", "increase", "up",
                ]):
                    return self._light_command(
                        "adjust", 10 if mild else 20,
                        original, text, 0.82,
                    )

                if self._has(text, [
                    "less", "dimmer", "lower", "decrease", "down",
                ]):
                    return self._light_command(
                        "adjust", -10 if mild else -20,
                        original, text, 0.82,
                    )

        on_phrases = [
            "turn on", "switch on", "lights on",
            "light on", "led on", "lamp on",
            "turn it on", "switch it on",
            "illuminate", "enable the light",
            "activate the light",
        ]

        off_phrases = [
            "turn off", "switch off", "lights off",
            "light off", "led off", "lamp off",
            "turn it off", "switch it off",
            "kill the light", "extinguish the light",
            "disable the light", "shut the light",
        ]

        if has_light_reference or self._has(text, self.LIGHT_WORDS):
            if any(phrase in text for phrase in off_phrases):
                return self._light_command(
                    "off", "", original, text, 0.98
                )

            if any(phrase in text for phrase in on_phrases):
                return self._light_command(
                    "on", "", original, text, 0.98
                )

        if recent_light_context:
            if re.search(r"\b(turn|switch)\s+(it|that)\s+off\b", text):
                return self._light_command(
                    "off", "", original, text, 0.92
                )

            if re.search(r"\b(turn|switch)\s+(it|that)\s+on\b", text):
                return self._light_command(
                    "on", "", original, text, 0.92
                )

        return ParsedCommand(
            "unknown",
            original_text=original,
            normalized_text=text,
            confidence=0.0,
        )

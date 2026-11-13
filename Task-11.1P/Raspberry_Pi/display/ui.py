import math
import random
import time
import threading
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from display.ili9341 import ILI9341

class DeskBotUI:
    """Expressive, anti-aliased landscape face for monday."""

    WIDTH = 320
    HEIGHT = 240

    PANEL_X = 8
    PANEL_Y = 43
    PANEL_W = 304
    PANEL_H = 190
    AA = 2

    BG_RGB = (8, 8, 23)
    EYE_RGB = (241, 243, 255)
    EYE_EDGE_RGB = (183, 176, 220)
    SHADOW_RGB = (19, 14, 43)

    IRIS_RGB = (145, 112, 229)
    IRIS_RING_RGB = (75, 56, 148)
    IRIS_INNER_RGB = (192, 168, 255)

    PUPIL_RGB = (9, 8, 25)
    HIGHLIGHT_RGB = (255, 255, 255)

    BROW_RGB = (220, 210, 248)
    ACCENT_RGB = (181, 145, 255)
    MUTED_RGB = (145, 137, 183)

    FPS = 18.0
    FRAME_INTERVAL = 1.0 / FPS

    EYES_DURATION = 60
    CLOCK_DURATION = 30

    EXPRESSIONS = {
        "neutral":   (122, 88, 0, 122, 88, 0, 13, 13, 0.0, 0.0, 9, 0, 0),
        "happy":     (126, 62, 7, 126, 62, 7, 18, 18, 0.0, 0.0, 15, -2, 2),
        "curious":   (112, 84, 1, 132, 96, -3, 26, 8, -0.15, 0.15, 10, -4, 4),
        "surprised": (128, 104, -5, 128, 104, -5, 31, 31, 0.0, 0.0, 12, 0, 0),
        "sleepy":    (130, 53, 12, 130, 53, 12, 5, 5, 0.0, 0.0, 2, 0, 0),
        "angry":     (127, 65, 6, 127, 65, 6, 7, 7, 0.9, -0.9, 1, -2, 2),
        "playful":   (128, 72, 4, 116, 84, -1, 18, 23, 0.25, -0.15, 13, -5, 4),
        "confused":  (111, 91, 0, 130, 73, 5, 28, 5, -0.2, 0.45, 10, 4, -3),
        "smug":      (130, 63, 8, 115, 87, -2, 9, 19, 0.25, -0.1, 5, 3, -4),
        "excited":   (132, 99, -3, 132, 99, -3, 30, 30, 0.0, 0.0, 16, -2, 2),
    }

    EXPRESSION_WEIGHTS = {
        "neutral": 28,
        "happy": 18,
        "curious": 15,
        "surprised": 6,
        "sleepy": 7,
        "angry": 3,
        "playful": 9,
        "confused": 6,
        "smug": 4,
        "excited": 4,
    }

    def __init__(self):
        self.lcd = ILI9341()
        self.lock = threading.RLock()

        self.panel_size = (
            self.PANEL_W * self.AA,
            self.PANEL_H * self.AA
        )

        self.panel_image = Image.new(
            "RGB", self.panel_size, self.BG_RGB
        )
        self.draw = ImageDraw.Draw(self.panel_image)

        self.current = [
            float(v) for v in self.EXPRESSIONS["neutral"]
        ]
        self.target = list(self.current)

        self.expression = "neutral"
        self.wink_side = None
        self.expression_until = time.monotonic() + 7

        self.gaze_x = 0.0
        self.gaze_y = 0.0
        self.target_gaze_x = 0.0
        self.target_gaze_y = 0.0

        self.next_gaze = time.monotonic() + 2.5

        self.blink_start = None
        self.blink_duration = 0.21
        self.next_blink = time.monotonic() + 3.0

        self.listening = False
        self.speaking = False
        self.voice_level = 0.0
        self.face_vertical_shift = 0.0

        self.font_path = self._find_font()

        self.clock_font = ImageFont.truetype(
            self.font_path, 158
        )
        self.date_font = ImageFont.truetype(
            self.font_path, 32
        )
        self.label_font = ImageFont.truetype(
            self.font_path, 22
        )
        self.seconds_font = ImageFont.truetype(
            self.font_path, 35
        )
        self.voice_font = ImageFont.truetype(
            self.font_path, 18
        )

        self.lcd.fill_screen(
            ILI9341.rgb565(*self.BG_RGB)
        )

        print("monday face renderer initialized.")

    @staticmethod
    def _find_font():
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

        for path in candidates:
            try:
                with open(path, "rb"):
                    return path
            except OSError:
                continue

        raise RuntimeError(
            "No suitable TrueType font found. "
            "Install fonts-dejavu-core."
        )

    def _s(self, value):
        return int(round(value * self.AA))

    def _rounded_rect(
        self, box, radius, fill, outline=None, width=1
    ):
        scaled = tuple(self._s(v) for v in box)

        self.draw.rounded_rectangle(
            scaled,
            radius=self._s(radius),
            fill=fill,
            outline=outline,
            width=max(1, self._s(width)) if outline else 1,
        )

    def _draw_brow(
        self,
        cx,
        eye_top,
        eye_width,
        lift,
        tilt,
        arch,
        mirrored=False,
    ):
        half = eye_width * 0.34
        y_base = eye_top - 18 - lift * 0.30

        if not mirrored:
            y0 = y_base - tilt * 6
            y1 = y_base + tilt * 6
        else:
            y0 = y_base + tilt * 6
            y1 = y_base - tilt * 6

        p0 = (cx - half, y0)
        p1 = (cx - half * 0.35, y_base - arch)
        p2 = (cx + half * 0.35, y_base - arch)
        p3 = (cx + half, y1)

        points = []

        for i in range(25):
            t = i / 24
            mt = 1 - t

            x = (
                mt**3 * p0[0]
                + 3 * mt**2 * t * p1[0]
                + 3 * mt * t**2 * p2[0]
                + t**3 * p3[0]
            )

            y = (
                mt**3 * p0[1]
                + 3 * mt**2 * t * p1[1]
                + 3 * mt * t**2 * p2[1]
                + t**3 * p3[1]
            )

            points.append((self._s(x), self._s(y)))

        self.draw.line(
            points,
            fill=self.BROW_RGB,
            width=self._s(3.8),
            joint="curve",
        )

        radius = self._s(1.9)

        for px, py in (points[0], points[-1]):
            self.draw.ellipse(
                (
                    px - radius,
                    py - radius,
                    px + radius,
                    py + radius,
                ),
                fill=self.BROW_RGB,
            )

    def _draw_eye(
        self,
        cx,
        cy,
        width,
        height,
        gaze_x,
        gaze_y,
        openness,
    ):
        width = max(15.0, width)
        height = max(5.0, height * openness)

        if height < 13:
            y = cy + 1
            half = width * 0.43
            points = []

            for i in range(21):
                t = i / 20
                x = cx - half + 2 * half * t
                yy = y - 3 * math.sin(math.pi * t)

                points.append(
                    (self._s(x), self._s(yy))
                )

            self.draw.line(
                points,
                fill=self.EYE_RGB,
                width=self._s(4),
                joint="curve",
            )
            return

        x0 = cx - width / 2
        x1 = cx + width / 2
        y0 = cy - height / 2
        y1 = cy + height / 2

        radius = min(23, height * 0.27)

        self._rounded_rect(
            (x0 - 2, y0 + 2, x1 + 2, y1 + 3),
            radius + 2,
            self.SHADOW_RGB,
        )

        self._rounded_rect(
            (x0, y0, x1, y1),
            radius,
            self.EYE_RGB,
            outline=self.EYE_EDGE_RGB,
            width=1.2,
        )

        iris_rx = min(20, width * 0.17)
        iris_ry = min(25, height * 0.34)

        ix = cx + max(-13, min(13, gaze_x))
        iy = cy + max(
            -height * 0.12,
            min(height * 0.12, gaze_y)
        )

        ix = max(
            x0 + iris_rx + 5,
            min(x1 - iris_rx - 5, ix)
        )

        iy = max(
            y0 + iris_ry + 3,
            min(y1 - iris_ry - 3, iy)
        )

        self.draw.ellipse(
            (
                self._s(ix - iris_rx - 2),
                self._s(iy - iris_ry - 2),
                self._s(ix + iris_rx + 2),
                self._s(iy + iris_ry + 2),
            ),
            fill=self.IRIS_RING_RGB,
        )

        self.draw.ellipse(
            (
                self._s(ix - iris_rx),
                self._s(iy - iris_ry),
                self._s(ix + iris_rx),
                self._s(iy + iris_ry),
            ),
            fill=self.IRIS_RGB,
        )

        self.draw.ellipse(
            (
                self._s(ix - iris_rx * 0.68),
                self._s(iy - iris_ry * 0.72),
                self._s(ix + iris_rx * 0.68),
                self._s(iy + iris_ry * 0.72),
            ),
            fill=self.IRIS_INNER_RGB,
        )

        pupil_rx = iris_rx * 0.43
        pupil_ry = iris_ry * 0.61

        self.draw.ellipse(
            (
                self._s(ix - pupil_rx),
                self._s(iy - pupil_ry),
                self._s(ix + pupil_rx),
                self._s(iy + pupil_ry),
            ),
            fill=self.PUPIL_RGB,
        )

        self.draw.ellipse(
            (
                self._s(ix - iris_rx * 0.48),
                self._s(iy - iris_ry * 0.57),
                self._s(ix - iris_rx * 0.13),
                self._s(iy - iris_ry * 0.22),
            ),
            fill=self.HIGHLIGHT_RGB,
        )

        self.draw.ellipse(
            (
                self._s(ix + iris_rx * 0.35),
                self._s(iy + iris_ry * 0.36),
                self._s(ix + iris_rx * 0.55),
                self._s(iy + iris_ry * 0.56),
            ),
            fill=self.HIGHLIGHT_RGB,
        )

        if height < 72:
            lid_h = min(12, (72 - height) * 0.22)

            if lid_h > 1:
                self._rounded_rect(
                    (x0 + 2, y0 + 1, x1 - 2, y0 + lid_h),
                    min(10, radius),
                    self.BG_RGB,
                )

                self.draw.line(
                    [
                        (self._s(x0 + 8), self._s(y0 + lid_h)),
                        (self._s(cx), self._s(y0 + lid_h + 2)),
                        (self._s(x1 - 8), self._s(y0 + lid_h)),
                    ],
                    fill=self.EYE_EDGE_RGB,
                    width=self._s(1.2),
                )

    def _rgb565_bytes(self, image):
        rgb = np.asarray(image, dtype=np.uint16)

        r = rgb[:, :, 0]
        g = rgb[:, :, 1]
        b = rgb[:, :, 2]

        packed = (
            ((r >> 3) << 11)
            | ((g >> 2) << 5)
            | (b >> 3)
        )

        return packed.astype(">u2", copy=False).tobytes()

    def render_face(self, openness):
        self.panel_image = Image.new(
            "RGB",
            self.panel_size,
            self.BG_RGB,
        )

        self.draw = ImageDraw.Draw(self.panel_image)

        (
            left_w,
            left_h,
            left_y,
            right_w,
            right_h,
            right_y,
            left_lift,
            right_lift,
            left_tilt,
            right_tilt,
            brow_arch,
            left_shift,
            right_shift,
        ) = self.current

        left_cx = 76 + left_shift
        right_cx = 228 + right_shift

        base_cy = 92 - self.face_vertical_shift

        left_cy = base_cy + left_y
        right_cy = base_cy + right_y

        self._draw_brow(
            left_cx,
            left_cy - left_h / 2,
            left_w,
            left_lift,
            left_tilt,
            brow_arch,
            mirrored=False,
        )

        self._draw_brow(
            right_cx,
            right_cy - right_h / 2,
            right_w,
            right_lift,
            right_tilt,
            brow_arch,
            mirrored=True,
        )

        left_open = openness
        right_open = openness

        now = time.monotonic()

        if self.expression == "wink" and now < self.wink_until:
            if self.wink_side == "left":
                left_open = 0.015
            else:
                right_open = 0.015

        self._draw_eye(
            left_cx,
            left_cy,
            left_w,
            left_h,
            self.gaze_x,
            self.gaze_y,
            left_open,
        )

        self._draw_eye(
            right_cx,
            right_cy,
            right_w,
            right_h,
            self.gaze_x,
            self.gaze_y,
            right_open,
        )

        if self.listening or self.speaking:
            self._draw_voice_wave()

        frame = self.panel_image.resize(
            (self.PANEL_W, self.PANEL_H),
            Image.Resampling.LANCZOS,
        )

        payload = self._rgb565_bytes(frame)

        self.lcd.blit(
            self.PANEL_X,
            self.PANEL_Y,
            self.PANEL_W,
            self.PANEL_H,
            payload,
        )

    def _draw_voice_wave(self):
        """
        Draw a live microphone-reactive waveform below the eyes.

        The voice recognition module must call:
            set_listening(True)
            set_voice_level(normalized_microphone_volume)

        The eyes automatically move upwards when voice mode starts.
        """

        now = time.monotonic()

        center_x = self.PANEL_W // 2
        center_y = 158

        bar_count = 27
        spacing = 6
        bar_width = 3

        if self.listening:
            level = max(0.035, min(1.0, self.voice_level))
            label = "LISTENING"

        else:
            level = (
                0.24
                + 0.13 * (
                    0.5 + 0.5 * math.sin(now * 3.8)
                )
            )
            label = "SPEAKING"

        for index in range(bar_count):
            offset = index - (bar_count - 1) / 2

            envelope = (
                0.20
                + 0.80 * math.exp(-((offset / 9.0) ** 2))
            )

            oscillation = (
                0.28
                + 0.72 * abs(
                    math.sin(now * 8.0 + index * 0.57)
                )
            )

            height = (
                2.0
                + 23.0 * level * envelope * oscillation
            )

            x = center_x + int(offset * spacing)
            y = center_y - height / 2

            self.draw.rounded_rectangle(
                (
                    self._s(x),
                    self._s(y),
                    self._s(x + bar_width),
                    self._s(y + height),
                ),
                radius=self._s(1.5),
                fill=self.ACCENT_RGB,
            )

        bbox = self.draw.textbbox(
            (0, 0),
            label,
            font=self.voice_font,
        )

        text_width = bbox[2] - bbox[0]

        self.draw.text(
            (
                self._s(center_x) - text_width // 2,
                self._s(177),
            ),
            label,
            font=self.voice_font,
            fill=self.MUTED_RGB,
        )

    def _draw_brand_label(self):
        """Draw monday in small lowercase lettering."""

        width = 100
        height = 20

        image = Image.new(
            "RGB",
            (width * self.AA, height * self.AA),
            self.BG_RGB,
        )

        draw = ImageDraw.Draw(image)

        text = "monday"

        bbox = draw.textbbox(
            (0, 0),
            text,
            font=self.label_font,
        )

        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        x = (width * self.AA - text_width) // 2
        y = (
            (height * self.AA - text_height) // 2
            - bbox[1]
        )

        draw.text(
            (x, y),
            text,
            font=self.label_font,
            fill=self.ACCENT_RGB,
        )

        frame = image.resize(
            (width, height),
            Image.Resampling.LANCZOS,
        )

        self.lcd.blit(
            (self.WIDTH - width) // 2,
            9,
            width,
            height,
            self._rgb565_bytes(frame),
        )

    def choose_expression(self, now):
        names = list(self.EXPRESSION_WEIGHTS.keys())
        weights = list(self.EXPRESSION_WEIGHTS.values())

        selected = random.choices(
            names,
            weights=weights,
            k=1,
        )[0]

        if selected == self.expression:
            selected = random.choice(
                [
                    name
                    for name in names
                    if name != self.expression
                ]
            )

        self.expression = selected
        params = list(self.EXPRESSIONS[selected])

        if selected == "wink":
            self.wink_side = random.choice(
                ["left", "right"]
            )

            if self.wink_side == "left":
                params[1] = 7
            else:
                params[4] = 7

            self.wink_until = now + random.uniform(0.8, 1.4)

        else:
            self.wink_side = None
            self.wink_until = 0

        self.target = params
        self.expression_until = now + random.uniform(6, 12)

        if selected == "curious":
            self.target_gaze_x = random.choice([-11, 11])
            self.target_gaze_y = random.choice([-3, 2])

        elif selected == "sleepy":
            self.target_gaze_x = 0
            self.target_gaze_y = 5

        elif selected == "playful":
            self.target_gaze_x = random.choice([-10, 10])
            self.target_gaze_y = -2

        elif selected in ("surprised", "excited"):
            self.target_gaze_x = 0
            self.target_gaze_y = 0

    def update_animation(self, now, dt):
        if now >= self.expression_until:
            self.choose_expression(now)

        alpha = 1.0 - math.exp(-dt * 3.8)

        for index in range(len(self.current)):
            self.current[index] += (
                self.target[index] - self.current[index]
            ) * alpha

        if now >= self.next_gaze:
            self.target_gaze_x = random.uniform(-11, 11)
            self.target_gaze_y = random.uniform(-5, 5)

            self.next_gaze = (
                now + random.uniform(2.5, 5.5)
            )

        gaze_alpha = 1.0 - math.exp(-dt * 4.2)

        self.gaze_x += (
            self.target_gaze_x - self.gaze_x
        ) * gaze_alpha

        self.gaze_y += (
            self.target_gaze_y - self.gaze_y
        ) * gaze_alpha

        if self.blink_start is None and now >= self.next_blink:
            self.blink_start = now
            self.blink_duration = random.uniform(0.19, 0.26)

            self.next_blink = (
                now + random.uniform(3.4, 6.2)
            )

        openness = 1.0

        if self.blink_start is not None:
            progress = (
                now - self.blink_start
            ) / self.blink_duration

            if progress >= 1.0:
                self.blink_start = None

            else:
                openness = abs(
                    math.cos(math.pi * progress)
                )

        return openness

    def _load_clock_fonts(self):
        path = self.font_path

        return (
            ImageFont.truetype(path, 154),
            ImageFont.truetype(path, 30),
            ImageFont.truetype(path, 22),
            ImageFont.truetype(path, 38),
        )

    def draw_clock(self, now=None):
        now = now or datetime.now()

        scale = 2

        image = Image.new(
            "RGB",
            (
                self.WIDTH * scale,
                self.HEIGHT * scale,
            ),
            self.BG_RGB,
        )

        draw = ImageDraw.Draw(image)

        (
            main_font,
            date_font,
            label_font,
            seconds_font,
        ) = self._load_clock_fonts()

        def center_text(text, y, font, fill):
            bbox = draw.textbbox(
                (0, 0),
                text,
                font=font,
            )

            width = bbox[2] - bbox[0]

            x = (
                self.WIDTH * scale - width
            ) // 2

            draw.text(
                (x, y * scale),
                text,
                font=font,
                fill=fill,
            )

        center_text(
            "monday",
            23,
            label_font,
            self.ACCENT_RGB,
        )

        draw.rounded_rectangle(
            (
                self._s(143),
                self._s(49),
                self._s(177),
                self._s(51),
            ),
            radius=self._s(1),
            fill=self.ACCENT_RGB,
        )

        hours = now.strftime("%H")
        minutes = now.strftime("%M")

        colon = ":"

        hour_w = draw.textlength(
            hours,
            font=main_font,
        )

        colon_w = draw.textlength(
            colon,
            font=main_font,
        )

        minute_w = draw.textlength(
            minutes,
            font=main_font,
        )

        total_w = (
            hour_w
            + colon_w
            + minute_w
            + self._s(3)
        )

        x = (
            self.WIDTH * scale - total_w
        ) // 2

        y = self._s(67)

        draw.text(
            (x + self._s(2), y + self._s(2)),
            hours,
            font=main_font,
            fill=(27, 19, 58),
        )

        draw.text(
            (
                x + hour_w + self._s(3),
                y + self._s(2),
            ),
            colon,
            font=main_font,
            fill=(39, 27, 78),
        )

        draw.text(
            (
                x + hour_w + colon_w + self._s(3),
                y + self._s(2),
            ),
            minutes,
            font=main_font,
            fill=(27, 19, 58),
        )

        draw.text(
            (x, y),
            hours,
            font=main_font,
            fill=(239, 247, 251),
        )

        draw.text(
            (
                x + hour_w + self._s(3),
                y,
            ),
            colon,
            font=main_font,
            fill=self.ACCENT_RGB,
        )

        draw.text(
            (
                x + hour_w + colon_w + self._s(3),
                y,
            ),
            minutes,
            font=main_font,
            fill=(239, 247, 251),
        )

        sec_text = now.strftime("%S")

        sec_bbox = draw.textbbox(
            (0, 0),
            sec_text,
            font=seconds_font,
        )

        sec_w = sec_bbox[2] - sec_bbox[0]

        draw.text(
            (
                (self.WIDTH * scale - sec_w) // 2,
                self._s(151),
            ),
            sec_text,
            font=seconds_font,
            fill=self.ACCENT_RGB,
        )

        date_text = now.strftime(
            "%A  ·  %d %B %Y"
        ).upper()

        date_bbox = draw.textbbox(
            (0, 0),
            date_text,
            font=date_font,
        )

        date_w = date_bbox[2] - date_bbox[0]
        date_font_fit = date_font

        if date_w > self.WIDTH * scale - self._s(16):
            date_font_fit = ImageFont.truetype(
                self.font_path,
                25,
            )

            date_bbox = draw.textbbox(
                (0, 0),
                date_text,
                font=date_font_fit,
            )

            date_w = date_bbox[2] - date_bbox[0]

        date_x = (
            self.WIDTH * scale - date_w
        ) // 2

        draw.text(
            (date_x, self._s(199)),
            date_text,
            font=date_font_fit,
            fill=self.MUTED_RGB,
        )

        frame = image.resize(
            (self.WIDTH, self.HEIGHT),
            Image.Resampling.LANCZOS,
        )

        payload = self._rgb565_bytes(frame)

        self.lcd.blit(
            0,
            0,
            self.WIDTH,
            self.HEIGHT,
            payload,
        )

    def set_listening(self, active=True):
        """
        Call when the wake word is detected or listening ends.
        """

        with self.lock:
            self.listening = bool(active)

            if active:
                self.speaking = False

    def set_speaking(self, active=True):
        """
        Call when monday starts or stops speaking.
        """

        with self.lock:
            self.speaking = bool(active)

            if active:
                self.listening = False

    def set_voice_level(self, level):
        """
        Pass normalized microphone RMS level between 0.0 and 1.0.
        """

        with self.lock:
            self.voice_level = max(
                0.0,
                min(1.0, float(level)),
            )

    def run(self):
        print("monday expressive face running.")
        print("Landscape rounded-rectangle eyes.")
        print("Eyes: 60 seconds | Live clock: 30 seconds.")
        print("Voice-reactive waveform interface ready.")
        print("Press Ctrl+C to stop.")

        try:
            while True:
                now = time.monotonic()

                self.expression = "neutral"

                self.current = [
                    float(v)
                    for v in self.EXPRESSIONS["neutral"]
                ]

                self.target = list(self.current)

                self.expression_until = (
                    now + random.uniform(5, 8)
                )

                self.next_gaze = now + 2.5
                self.next_blink = now + 3.0

                self.blink_start = None

                self.gaze_x = 0.0
                self.gaze_y = 0.0
                self.target_gaze_x = 0.0
                self.target_gaze_y = 0.0

                self.face_vertical_shift = 0.0

                self.lcd.fill_screen(
                    ILI9341.rgb565(*self.BG_RGB)
                )

                eyes_start = time.monotonic()
                previous = time.monotonic()

                while (
                    time.monotonic() - eyes_start < self.EYES_DURATION
                    or self.listening
                    or self.speaking
                ):
                    frame_start = time.monotonic()

                    dt = min(
                        0.1,
                        frame_start - previous,
                    )

                    previous = frame_start

                    with self.lock:
                        voice_active = (
                            self.listening or self.speaking
                        )

                        target_shift = (
                            15.0 if voice_active else 0.0
                        )

                        shift_alpha = (
                            1.0 - math.exp(-dt * 6.0)
                        )

                        self.face_vertical_shift += (
                            target_shift - self.face_vertical_shift
                        ) * shift_alpha

                        openness = self.update_animation(
                            frame_start,
                            dt,
                        )

                        self.render_face(openness)

                    elapsed = (
                        time.monotonic() - frame_start
                    )

                    time.sleep(
                        max(
                            0.0,
                            self.FRAME_INTERVAL - elapsed,
                        )
                    )

                clock_start = time.monotonic()
                last_second = None

                while (
                    time.monotonic() - clock_start
                    < self.CLOCK_DURATION
                ):
                    with self.lock:
                        if self.listening or self.speaking:
                            break

                    now = datetime.now()

                    second_key = now.strftime(
                        "%Y%m%d%H%M%S"
                    )

                    if second_key != last_second:
                        self.draw_clock(now)
                        last_second = second_key

                    time.sleep(0.08)

        except KeyboardInterrupt:
            print("\nStopping monday display.")

        finally:
            self.lcd.fill_screen(
                ILI9341.rgb565(*self.BG_RGB)
            )

            self.lcd.close()

            print("monday display safely stopped.")

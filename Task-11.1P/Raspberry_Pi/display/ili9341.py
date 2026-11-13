import time
import spidev
import lgpio

class ILI9341:
    DC_PIN = 25
    RST_PIN = 27
    BL_PIN = 23

    WIDTH = 320
    HEIGHT = 240

    def __init__(self, bus=0, device=0, speed=24000000):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = speed
        self.spi.mode = 0

        self.gpio = lgpio.gpiochip_open(0)

        try:
            lgpio.gpio_claim_output(self.gpio, self.DC_PIN, 0)
            lgpio.gpio_claim_output(self.gpio, self.RST_PIN, 1)
            lgpio.gpio_claim_output(self.gpio, self.BL_PIN, 0)

            self.reset()
            self.initialize()
            self.backlight(True)

        except Exception:
            self.spi.close()
            lgpio.gpiochip_close(self.gpio)
            raise

    def command(self, cmd):
        lgpio.gpio_write(self.gpio, self.DC_PIN, 0)
        self.spi.writebytes([cmd])

    def data(self, values):
        lgpio.gpio_write(self.gpio, self.DC_PIN, 1)
        self.spi.writebytes(list(values))

    def reset(self):
        lgpio.gpio_write(self.gpio, self.RST_PIN, 1)
        time.sleep(0.05)
        lgpio.gpio_write(self.gpio, self.RST_PIN, 0)
        time.sleep(0.05)
        lgpio.gpio_write(self.gpio, self.RST_PIN, 1)
        time.sleep(0.15)

    def initialize(self):
        self.command(0x01)
        time.sleep(0.15)

        self.command(0x28)

        self.command(0xCF)
        self.data([0x00, 0xC1, 0x30])

        self.command(0xED)
        self.data([0x64, 0x03, 0x12, 0x81])

        self.command(0xE8)
        self.data([0x85, 0x00, 0x78])

        self.command(0xCB)
        self.data([0x39, 0x2C, 0x00, 0x34, 0x02])

        self.command(0xF7)
        self.data([0x20])

        self.command(0xEA)
        self.data([0x00, 0x00])

        self.command(0xC0)
        self.data([0x23])

        self.command(0xC1)
        self.data([0x10])

        self.command(0xC5)
        self.data([0x3E, 0x28])

        self.command(0xC7)
        self.data([0x86])

        self.command(0x36)
        self.data([0x28])

        self.command(0x3A)
        self.data([0x55])

        self.command(0xB1)
        self.data([0x00, 0x18])

        self.command(0xB6)
        self.data([0x08, 0x82, 0x27])

        self.command(0xF2)
        self.data([0x00])

        self.command(0x26)
        self.data([0x01])

        self.command(0xE0)
        self.data([
            0x0F, 0x31, 0x2B, 0x0C, 0x0E,
            0x08, 0x4E, 0xF1, 0x37, 0x07,
            0x10, 0x03, 0x0E, 0x09, 0x00
        ])

        self.command(0xE1)
        self.data([
            0x00, 0x0E, 0x14, 0x03, 0x11,
            0x07, 0x31, 0xC1, 0x48, 0x08,
            0x0F, 0x0C, 0x31, 0x36, 0x0F
        ])

        self.command(0x11)
        time.sleep(0.12)

        self.command(0x29)
        time.sleep(0.02)

    def backlight(self, enabled=True):
        lgpio.gpio_write(
            self.gpio, self.BL_PIN, int(enabled)
        )

    @staticmethod
    def rgb565(red, green, blue):
        return (
            ((red & 0xF8) << 8)
            | ((green & 0xFC) << 3)
            | (blue >> 3)
        )

    def set_window(self, x0, y0, x1, y1):
        self.command(0x2A)
        self.data([
            (x0 >> 8) & 255, x0 & 255,
            (x1 >> 8) & 255, x1 & 255
        ])

        self.command(0x2B)
        self.data([
            (y0 >> 8) & 255, y0 & 255,
            (y1 >> 8) & 255, y1 & 255
        ])

        self.command(0x2C)

    def blit(self, x, y, width, height, pixels):
        """Transfer one complete RGB565 framebuffer region."""
        if len(pixels) != width * height * 2:
            raise ValueError("Framebuffer size does not match dimensions")

        self.set_window(
            x, y,
            x + width - 1,
            y + height - 1
        )

        lgpio.gpio_write(self.gpio, self.DC_PIN, 1)

        if hasattr(self.spi, "writebytes2"):
            self.spi.writebytes2(pixels)
        else:
            chunk_size = 4096
            for start in range(0, len(pixels), chunk_size):
                self.spi.xfer2(
                    list(pixels[start:start + chunk_size])
                )

    def fill_rect(self, x, y, width, height, color):
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.WIDTH, x + width)
        y1 = min(self.HEIGHT, y + height)

        if x0 >= x1 or y0 >= y1:
            return

        high = (color >> 8) & 255
        low = color & 255
        row = [high, low] * (x1 - x0)

        self.set_window(x0, y0, x1 - 1, y1 - 1)
        lgpio.gpio_write(self.gpio, self.DC_PIN, 1)

        for _ in range(y1 - y0):
            self.spi.writebytes2(bytearray(row))

    def fill_screen(self, color):
        self.fill_rect(0, 0, self.WIDTH, self.HEIGHT, color)

    def close(self):
        try:
            self.backlight(False)
            self.spi.close()

            for pin in (self.DC_PIN, self.RST_PIN, self.BL_PIN):
                try:
                    lgpio.gpio_free(self.gpio, pin)
                except Exception:
                    pass

            lgpio.gpiochip_close(self.gpio)

        except Exception as error:
            print(f"LCD shutdown warning: {error}")

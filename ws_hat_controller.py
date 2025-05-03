# -*- coding:utf-8 -*-
import json
import logging
import os.path as _p
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pprint import pprint
from typing import Deque, Dict, List, Literal, Tuple

import paho.mqtt.publish as publish
import RPi.GPIO as GPIO
import spidev as SPI
import yaml
from PIL import Image, ImageDraw, ImageFont

from waveshare import ST7789

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

HEAT_PLATE_RELAY_GPIO = 12
GPIO.setmode(GPIO.BCM)
GPIO.setup(HEAT_PLATE_RELAY_GPIO, GPIO.OUT)


with open("settings.yaml") as ifile:
    SETTINGS = yaml.safe_load(ifile)
pprint(SETTINGS)

MQTT_HOST = SETTINGS["mqtt"]["host"]
MQTT_PORT = SETTINGS["mqtt"]["port"]
SHOULD_PUSH_TO_MOSQUITTO = SETTINGS["mqtt"]["should_push"]


def mqtt_publish(broker_host: str, broker_port: int, topic: str, payload: str):
    """Publish an MQTT message using paho-mqtt in one shot."""
    try:
        publish.single(topic, payload=payload, hostname=broker_host, port=broker_port)
    except Exception as e:
        logger.warning("Failed to push MQTT message: %s", e)
        logger.debug("Failed payload: %s", payload)


class GlobalConfig:
    LCD_BRIGHTNESS_LEVELS = (0, 10, 70)
    LCD_BRIGHTNESS = LCD_BRIGHTNESS_LEVELS[1]


@dataclass
class Measurement:
    time: datetime
    raw_celsius: float
    calibrated_celsius: float


class TemperatureGetter:
    _singleton = None

    @classmethod
    def get_current_measurement(cls):
        if cls._singleton is None:
            cls._singleton = cls()
        latest_result = cls._singleton.get_readout()
        # temper2 also has internal temperature
        # latest_resultresult["internal temperature"],
        raw_celsius = latest_result.get("external temperature")
        if raw_celsius is None:
            calibrated_celsius = None
        else:
            calibrated_celsius = round(cls._singleton.apply_calibration(raw_celsius), 1)
        return Measurement(
            time=datetime.now(timezone.utc),
            raw_celsius=raw_celsius,
            calibrated_celsius=calibrated_celsius,
        )

    def __init__(self):
        from temper.temper import Temper

        self._temper = Temper()
        self.calibrate_sensor(SETTINGS["calibration_points"])

    def get_readout(self):
        results = self._temper.read()
        if not results:
            sys.stderr.write("could not read any results\n")
            sys.exit(-1)
        result = results[0]
        return result

    def apply_calibration(self, raw_temp: float) -> float:
        if self._calibration is None:
            return raw_temp
        slope, intercept = self._calibration
        return raw_temp * slope + intercept

    def calibrate_sensor(self, calibration_points: List[Tuple[float, float]]):
        """Given (sensor_readout, actual_temperature) pairs, compute calibration."""
        import numpy as np

        sensor_readouts, actual_temperatures = zip(*calibration_points)
        slope, intercept = np.polyfit(sensor_readouts, actual_temperatures, 1)
        self._calibration = (slope, intercept)


class Canvas:
    def __init__(
        self,
        width,
        height,
        bg_color="WHITE",
        default_font_path="/usr/share/fonts/truetype/freefont/FreeMono.ttf",
        default_font_size=12,
    ):
        self.image = Image.new("RGB", (width, height), bg_color)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}

        self.temperature_records: Deque[Measurement] = deque(maxlen=100)

        # Attempt to load the default font immediately
        try:
            self.default_font_key = self.load_font(default_font_path, default_font_size)
        except Exception as e:
            raise RuntimeError(
                f"Failed to load default font {default_font_name} size {default_font_size}: {e}"
            )

    def load_font(self, font_path: str, font_size: float) -> Tuple[str, float]:
        """Load a font once and store it under a name."""
        if not _p.exists(font_path):
            raise FileNotFoundError(f"Font file not found: {font_path}")
        font_name = _p.splitext(_p.split(font_path)[1])[0]
        font_key = (font_name, font_size)
        self.fonts[font_key] = ImageFont.truetype(font_path, font_size)
        return font_key

    def draw_text_block(
        self,
        text,
        pos,
        size,
        font_name=None,
        font_size=None,
        text_color="BLACK",
        bg_color=None,
    ):
        """Draw text with optional background, using preloaded font or default."""
        if font_name is None or font_size is None:
            font_key = self.default_font_key
        else:
            font_key = (font_name, font_size)

        if font_key not in self.fonts:
            logging.warning(
                f"Font {font_key} not loaded, falling back to default font {self.default_font_key}"
            )
            font_key = self.default_font_key

        x0, y0 = pos
        w, h = size

        if bg_color:
            self.draw.rectangle([x0, y0, x0 + w, y0 + h], fill=bg_color)

        font = self.fonts[font_key]
        self.draw.text((x0 + 5, y0 + 3), text, fill=text_color, font=font)

    def clear(self, bg_color="WHITE"):
        """Clear the entire canvas."""
        self.draw.rectangle([(0, 0), self.image.size], fill=bg_color)

    def render(self, disp, rotate_angle=0, brightness=None):
        """Send the current canvas to the display."""
        rotated_image = self.image.rotate(rotate_angle)
        if brightness is not None:
            disp.bl_DutyCycle(brightness)
        else:
            disp.bl_DutyCycle(GlobalConfig.LCD_BRIGHTNESS)
        disp.ShowImage(rotated_image)

    def draw_button(
        self, shape_type, shape_data, pressed, color_pressed=0, color_released=0xFF00
    ):
        """Draw a button in pressed/released state."""
        if shape_type == "polygon":
            self.draw.polygon(
                shape_data,
                outline=255,
                fill=color_pressed if pressed else color_released,
            )
        elif shape_type == "rectangle":
            self.draw.rectangle(
                shape_data,
                outline=255,
                fill=color_pressed if pressed else color_released,
            )
        elif shape_type == "ellipse":
            self.draw.ellipse(
                shape_data,
                outline=255,
                fill=color_pressed if pressed else color_released,
            )
        else:
            raise ValueError(f"Unsupported shape {shape_type}")

    def draw_temperature_sparkline(
        self,
        pos,
        size,
        min_temp=20.0,
        max_temp=60.0,
        point_style="square",
        point_color="BLACK",
        grid_color=(200, 200, 200),
    ):
        """Draw a simple sparkline from temperature_records at the given position, with optional grid lines."""
        x0, y0 = pos
        w, h = size

        if not self.temperature_records:
            logging.info("No temperature records to draw.")
            return

        # Draw horizontal grid lines
        self.draw.line([(x0, y0), (x0 + w, y0)], fill=(0, 255, 255))
        self.draw.line([(x0, y0 + h), (x0 + w, y0 + h)], fill=(0, 0, 255))
        for grid_temp in range(int(min_temp) + 10, int(max_temp), 10):
            norm = (grid_temp - min_temp) / (max_temp - min_temp)
            norm = max(0.0, min(1.0, norm))  # clamp
            y = y0 + h - int(norm * h)
            self.draw.line([(x0, y), (x0 + w, y)], fill=grid_color)

        num_points = len(self.temperature_records)
        if num_points == 0:
            return

        x_step = w / max(num_points - 1, 1)  # avoid div0

        for idx, measurement in enumerate(self.temperature_records):
            temp = measurement.calibrated_celsius
            if temp is None:
                continue
            norm = (temp - min_temp) / (max_temp - min_temp)
            norm = max(0.0, min(1.0, norm))  # clamp

            y = y0 + h - int(norm * h)  # invert y so higher temp is higher up
            x = int(x0 + idx * x_step)

            if point_style == "square":
                self.draw.rectangle([x - 1, y - 1, x + 1, y + 1], fill=point_color)
            elif point_style == "circle":
                self.draw.ellipse([x - 1, y - 1, x + 1, y + 1], fill=point_color)
            else:
                raise ValueError(f"Unknown point style: {point_style}")


class HeatingMode:
    NAME: str
    upper_limit: float
    lower_limit: float

    def __init__(
        self, name: str, lower: float, upper: float, display_name: str | None = None
    ):
        self.NAME = name
        self.lower_limit = lower
        self.upper_limit = upper
        self.display_name = display_name or name

    def __str__(self):
        return f"[{self.display_name}] {self.lower_limit:.2f} ~ {self.upper_limit:.2f}"


class HeatingController:
    _instance = None  # Singleton instance

    AVAILABLE_MODES = [
        HeatingMode("natto", 39.8, 40.5),
        HeatingMode("greek yogurt", 42.0, 43.0, display_name="greek"),
        HeatingMode("free", 0.0, 100.0),
    ]

    _power_status: Literal["on", "off"]

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls("free")
        return cls._instance

    def __init__(self, mode: str):
        # Private constructor
        matching_modes = [m for m in self.AVAILABLE_MODES if m.NAME == mode]
        if not matching_modes:
            raise ValueError(f"No heating mode named '{mode}' found.")
        self._current_mode = matching_modes[0]
        self.turn_on()

    def change_to_mode(self, new_mode: str):
        matching_modes = [m for m in self.AVAILABLE_MODES if m.NAME == new_mode]
        if not matching_modes:
            raise ValueError(f"No heating mode named '{new_mode}' found.")
        self._current_mode = matching_modes[0]

    def get_current_heating_mode(self) -> HeatingMode:
        return self._current_mode

    def get_power_status(self):
        return self._power_status

    # NOTE we have a relay with internal pull-up
    # so ANY floating signal leads it to turn on
    # so to turn off, we shut the GPIO
    def turn_off(self):
        self._power_status = "off"
        GPIO.setup(HEAT_PLATE_RELAY_GPIO, GPIO.IN)

    def turn_on(self):
        self._power_status = "on"
        GPIO.setup(HEAT_PLATE_RELAY_GPIO, GPIO.OUT)


# 240x240 display with hardware SPI:
disp = ST7789.ST7789()
disp.Init()

# Clear display.
disp.clear()


def handle_key_1(button_name, button_config):
    current_brightness_index = GlobalConfig.LCD_BRIGHTNESS_LEVELS.index(
        GlobalConfig.LCD_BRIGHTNESS
    )
    next_index = (current_brightness_index + 1) % len(
        GlobalConfig.LCD_BRIGHTNESS_LEVELS
    )
    GlobalConfig.LCD_BRIGHTNESS = GlobalConfig.LCD_BRIGHTNESS_LEVELS[next_index]


def handle_key_2(button_name, button_config):
    current_mode_index = HeatingController.AVAILABLE_MODES.index(
        HeatingController.get_instance().get_current_heating_mode()
    )
    next_index = (current_mode_index + 1) % len(HeatingController.AVAILABLE_MODES)
    HeatingController.get_instance().change_to_mode(
        HeatingController.AVAILABLE_MODES[next_index].NAME
    )
    logger.info(
        "key 2 %s, new mode: %s",
        button_name,
        HeatingController.get_instance().get_current_heating_mode(),
    )


def handle_key_3(button_name, button_config):
    current_power_status = HeatingController.get_instance().get_power_status()
    if current_power_status == "off":
        HeatingController.get_instance().turn_on()
    else:
        HeatingController.get_instance().turn_off()
    logger.info("relay status: %s", HeatingController.get_instance().get_power_status())


BUTTON_CONFIG = {
    "UP": {
        "pin": disp.GPIO_KEY_UP_PIN,
        "shape": "polygon",
        "points": [(20, 20), (30, 2), (40, 20)],
    },
    "DOWN": {
        "pin": disp.GPIO_KEY_DOWN_PIN,
        "shape": "polygon",
        "points": [(30, 60), (40, 42), (20, 42)],
    },
    "LEFT": {
        "pin": disp.GPIO_KEY_LEFT_PIN,
        "shape": "polygon",
        "points": [(0, 30), (18, 21), (18, 41)],
    },
    "RIGHT": {
        "pin": disp.GPIO_KEY_RIGHT_PIN,
        "shape": "polygon",
        "points": [(60, 30), (42, 21), (42, 41)],
    },
    "CENTER": {
        "pin": disp.GPIO_KEY_PRESS_PIN,
        "shape": "rectangle",
        "bbox": (20, 22, 40, 40),
    },
    "KEY1": {
        "pin": disp.GPIO_KEY1_PIN,
        "shape": "ellipse",
        "bbox": (70, 0, 90, 20),
        "handler": handle_key_1,
    },
    "KEY2": {
        "pin": disp.GPIO_KEY2_PIN,
        "shape": "ellipse",
        "bbox": (100, 20, 120, 40),
        "handler": handle_key_2,
    },
    "KEY3": {
        "pin": disp.GPIO_KEY3_PIN,
        "shape": "ellipse",
        "bbox": (70, 40, 90, 60),
        "handler": handle_key_3,
    },
}


def poll_buttons(disp):
    states = {}
    for name, cfg in BUTTON_CONFIG.items():
        pressed = (
            disp.digital_read(cfg["pin"]) != 0
        )  # 0=released, 1=pressed in Waveshare logic
        states[name] = pressed
    return states


canvas = Canvas(disp.width, disp.height)
# Load extra fonts if needed

font0 = canvas.load_font("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 36)
font1 = canvas.load_font("/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf", 28)

TEMPERATURE_POLL_FREQUENCY_SECONDS = 10
LAST_POLL_TIME = time.time() - TEMPERATURE_POLL_FREQUENCY_SECONDS

try:
    while True:
        # 1. Poll button states
        button_states = poll_buttons(disp)

        # 2. Clear canvas
        canvas.clear(bg_color="WHITE")

        # 3. Draw all buttons based on state
        for name, cfg in BUTTON_CONFIG.items():
            pressed = button_states[name]
            if cfg["shape"] == "polygon":
                canvas.draw_button("polygon", cfg["points"], pressed)
            else:
                canvas.draw_button(cfg["shape"], cfg["bbox"], pressed)

            if pressed:
                maybe_handler = cfg.get("handler")
                print(name, maybe_handler)
                if maybe_handler is not None:
                    maybe_handler(name, cfg)

        tN = time.time()
        if tN - LAST_POLL_TIME > TEMPERATURE_POLL_FREQUENCY_SECONDS:
            LAST_POLL_TIME = tN

            current_heating_mode = (
                HeatingController.get_instance().get_current_heating_mode()
            )
            latest_measurement = TemperatureGetter.get_current_measurement()
            power_status = HeatingController.get_instance().get_power_status()

            if latest_measurement.raw_celsius is None:
                logger.warning("No temperature measurement available")
                time.sleep(5)
                continue
            canvas.temperature_records.append(latest_measurement)

            logger.info(
                "[%s] Temperature: %.2f°C (raw: %.2f°C), Power: %s",
                current_heating_mode,
                latest_measurement.calibrated_celsius,
                latest_measurement.raw_celsius,
                power_status,
            )

            if current_heating_mode.NAME == "natto":
                topic_postfix = "inside natto bowl"
            elif current_heating_mode.NAME == "yogurt":
                topic_postfix = "inside yogurt bowl"
            else:
                topic_postfix = current_heating_mode.NAME
            topic = f"environment/sensors/devices/{topic_postfix}"
            payload = {
                "temper_temperature": f"{latest_measurement.raw_celsius}C",
                "corrected_temperature": f"{latest_measurement.calibrated_celsius}C",
                "heat_plate_power": power_status,
            }
            if SHOULD_PUSH_TO_MOSQUITTO and current_heating_mode.NAME != "free":
                mqtt_publish(MQTT_HOST, MQTT_PORT, topic, json.dumps(payload))
            else:
                logger.debug("MQTT payload: %s", payload)

            if (
                power_status == "off"
                and latest_measurement.calibrated_celsius
                < current_heating_mode.lower_limit
            ):
                logger.info(
                    "Temperature too low (%.2f < %.2f); turning on",
                    latest_measurement.calibrated_celsius,
                    current_heating_mode.lower_limit,
                )
                HeatingController.get_instance().turn_on()
            elif (
                power_status == "on"
                and latest_measurement.calibrated_celsius
                > current_heating_mode.upper_limit
            ):
                logger.info(
                    "Temperature too high (%.2f > %.2f); turning off",
                    latest_measurement.calibrated_celsius,
                    current_heating_mode.upper_limit,
                )
                HeatingController.get_instance().turn_off()
            else:
                logger.debug(
                    "Temperature within bounds (%.2f < %.2f < %.2f)",
                    current_heating_mode.lower_limit,
                    latest_measurement.calibrated_celsius,
                    current_heating_mode.upper_limit,
                )

        canvas.draw_text_block(
            text=f"{current_heating_mode}",
            pos=(0, 115),
            size=(190, 45),
            font_name=font1[0],
            font_size=font1[1],
            text_color="RED",
        )

        if latest_measurement.raw_celsius is not None:
            # Draw blocks with optional font or fallback
            canvas.draw_text_block(
                text=f"{latest_measurement.calibrated_celsius:.2f} C",
                pos=(0, 65),
                size=(140, 35),
                font_name=font0[0],
                font_size=font0[1],
                text_color="BLACK",
                bg_color="WHITE",
            )

            # Draw temperature sparkline
            canvas.draw_temperature_sparkline(
                pos=(0, disp.height - 60 - 5),
                size=(disp.width, 60),  # size of the region
                point_style="square",
                point_color="BLACK",
            )

        # 4. Render to screen
        canvas.render(disp, 0)

        # this seems to freeze the device at some point!
        # disp.bl_DutyCycle(GlobalConfig.LCD_BRIGHTNESS)
        time.sleep(0.5)  # Small sleep for CPU relief (adjust frame rate)

except KeyboardInterrupt as e:
    logger.info("Exiting cleanly: %s", e)

except Exception as e:
    import traceback

    exc_type, exc_value, exc_traceback = sys.exc_info()
    if exc_traceback is not None:
        line_number = traceback.extract_tb(exc_traceback)[-1][1]
        filename = exc_traceback.tb_frame.f_code.co_filename
        lineno = exc_traceback.tb_lineno
        function_name = exc_traceback.tb_frame.f_code.co_name
        logger.error(
            "UNHANDLED EXCEPTION: %s:%d, in %s\n%s", filename, lineno, function_name, e
        )
        traceback.print_exception(exc_type, exc_value, exc_traceback)
    else:
        logger.error("UNHANDLED EXCEPTION: %s", e)

GPIO.cleanup()
disp.module_exit()

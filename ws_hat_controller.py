# -*- coding:utf-8 -*-
import sys
import spidev as SPI
import logging
from waveshare import ST7789
import os.path as _p
from typing import Tuple

import time
import subprocess

from PIL import Image,ImageDraw,ImageFont


FONT_DIRECTORY = _p.expanduser("~/Desktop/interactive-app/1.3inch_LCD_HAT_code/python/Font")

class Canvas:
    def __init__(self, width, height, bg_color="WHITE", default_font_name="Font00", default_font_size=12):
        self.image = Image.new("RGB", (width, height), bg_color)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}
        self.default_font_key = (default_font_name, default_font_size)
        
        # Attempt to load the default font immediately
        try:
            self.load_font(default_font_name, default_font_size)
        except Exception as e:
            raise RuntimeError(f"Failed to load default font {default_font_name} size {default_font_size}: {e}")

    def load_font(self, name, size) -> Tuple[str, int]:
        """Load a font once and store it under a name."""
        font_path = _p.join(FONT_DIRECTORY, f"{name}.ttf")
        if not _p.exists(font_path):
            raise FileNotFoundError(f"Font file not found: {font_path}")
        font_key = (name, size)
        self.fonts[font_key] = ImageFont.truetype(font_path, size)
        return font_key

    def draw_text_block(self, text, pos, size, font_name=None, font_size=None, text_color="BLACK", bg_color=None):
        """Draw text with optional background, using preloaded font or default."""
        if font_name is None or font_size is None:
            font_key = self.default_font_key
        else:
            font_key = (font_name, font_size)
        
        if font_key not in self.fonts:
            logging.warning(f"Font {font_key} not loaded, falling back to default font {self.default_font_key}")
            font_key = self.default_font_key

        x0, y0 = pos
        w, h = size

        if bg_color:
            self.draw.rectangle([x0, y0, x0 + w, y0 + h], fill=bg_color)

        font = self.fonts[font_key]
        self.draw.text((x0 + 5, y0 + 3), text, fill=text_color, font=font)

    def clear(self, bg_color="WHITE"):
        """Clear the entire canvas."""
        self.draw.rectangle([(0,0), self.image.size], fill=bg_color)

    def render(self, disp, rotate_angle=0, brightness=50):
        """Send the current canvas to the display."""
        rotated_image = self.image.rotate(rotate_angle)
        disp.bl_DutyCycle(brightness)
        disp.ShowImage(rotated_image)

    def draw_button(self, shape_type, shape_data, pressed, color_pressed=0, color_released=0xff00):
        """Draw a button in pressed/released state."""
        if shape_type == "polygon":
            self.draw.polygon(shape_data, outline=255, fill=color_pressed if pressed else color_released)
        elif shape_type == "rectangle":
            self.draw.rectangle(shape_data, outline=255, fill=color_pressed if pressed else color_released)
        elif shape_type == "ellipse":
            self.draw.ellipse(shape_data, outline=255, fill=color_pressed if pressed else color_released)
        else:
            raise ValueError(f"Unsupported shape {shape_type}")





if 0:
    from temper.temper import Temper

    temper = Temper()
    results = temper.read()
    if not results:
        sys.stderr.write("could not read any results\n")
        sys.exit(-1)

    result = results[0]
    print(
        result["internal temperature"],
        result["external temperature"],
    )


def get_temperature_sensor_temperature(*argvs):
    import random
    return 20 + 10 * random.random()


class HeatingMode:

    MODE_GREEK = 'greek yogurt'
    MODE_NATTO = 'natto'

    AVAILABLE_MODES = [
        MODE_GREEK,
        MODE_NATTO,
    ]

    def __init__(self):
        self._current_mode = HeatingMode.MODE_NATTO

    def change_to_mode(self, new_mode):
        self._current_mode = new_mode

    def get_current_heating_mode(self):
        return self._current_mode



# 240x240 display with hardware SPI:
disp = ST7789.ST7789()
disp.Init()

# Clear display.
disp.clear()
print("CLEARED")
# time.sleep(5)
##time.sleep(5)
##sys.exit()
##
###Set the backlight to 100
##for i in range(99):
##    disp.bl_DutyCycle(0)
##    time.sleep(0.05)


class GlobalConfig:
    LCD_BRIGHTNESS_LEVELS = (0, 10, 50, 80)
    LCD_BRIGHTNESS = 10


def handle_button(button_name, button_config):
    print("handling button", button_name)

def handle_up_button(button_name, button_config):
    print("up button", button_name)

def handle_key_1(button_name, button_config):
    current_brightness_index = GlobalConfig.LCD_BRIGHTNESS_LEVELS.index(GlobalConfig.LCD_BRIGHTNESS)
    next_index = (current_brightness_index + 1) % len(GlobalConfig.LCD_BRIGHTNESS_LEVELS)
    GlobalConfig.LCD_BRIGHTNESS = GlobalConfig.LCD_BRIGHTNESS_LEVELS[next_index]
    print("key 1", button_name, GlobalConfig.LCD_BRIGHTNESS)


BUTTON_CONFIG = {
    "UP": {
        "pin": disp.GPIO_KEY_UP_PIN,
        "shape": "polygon",
        "points": [(20, 20), (30, 2), (40, 20)],
        "handler": handle_up_button,
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
    },
    "KEY3": {
        "pin": disp.GPIO_KEY3_PIN,
        "shape": "ellipse",
        "bbox": (70, 40, 90, 60),
    },
}

def poll_buttons(disp):
    states = {}
    for name, cfg in BUTTON_CONFIG.items():
        pressed = disp.digital_read(cfg["pin"]) != 0  # 0=released, 1=pressed in Waveshare logic
        states[name] = pressed
    return states


import time

canvas = Canvas(disp.width, disp.height)
# Load extra fonts if needed
font0 = canvas.load_font("Font00", 30)
font1 = canvas.load_font("Font01", 50)


heating_control = HeatingMode()


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


        # Draw blocks with optional font or fallback
        canvas.draw_text_block(
            text=f"{get_temperature_sensor_temperature():.2f} C",
            pos=(0, 65),
            size=(140, 35),
            font_name=font0[0],
            font_size=font0[1],
            text_color="BLACK",
            bg_color="WHITE"
        )

        canvas.draw_text_block(
            text=f"{heating_control.get_current_heating_mode()}",
            pos=(0, 115),
            size=(190, 45),
            font_name=font1[0],
            font_size=font1[1],
            text_color="RED",
        )
        import random
        heating_control.change_to_mode(random.choice(HeatingMode.AVAILABLE_MODES))

        canvas.draw_text_block(
            text="Fallback font example",
            pos=(0, 180),
            size=(190, 45),
            text_color="BLACK",
            bg_color="YELLOW"
        )  # No font specified, uses default Font01 12pt

        # 4. Render to screen
        canvas.render(disp, 0, 10)

        time.sleep(1)  # Small sleep for CPU relief (adjust frame rate)
        disp.bl_DutyCycle(GlobalConfig.LCD_BRIGHTNESS)
        time.sleep(5)
except KeyboardInterrupt as e:
    print("Exiting cleanly.", e)


disp.module_exit()



"""
Kivy UI configuration and constants.
All connection settings can be overridden via environment variables
or the in-app settings screen.
"""
import os

WS_URL   = os.environ.get("JUAN_WS_URL",   "ws://localhost:5001")
HTTP_URL = os.environ.get("JUAN_HTTP_URL", "http://localhost:5002")
PASSWORD = os.environ.get("JUAN_PASSWORD", "")

# Token storage path (platform-aware)
import pathlib

_config_dir = pathlib.Path.home() / ".juan"
_config_dir.mkdir(exist_ok=True)
TOKEN_FILE = _config_dir / "kivy_token.json"
SETTINGS_FILE = _config_dir / "kivy_settings.json"

# UI constants
APP_TITLE    = "Juan"
APP_VERSION  = "1.0.0"
FONT_SIZE_SM = 13
FONT_SIZE_MD = 15
FONT_SIZE_LG = 17

# Colours matching React UI dark theme
COLORS = {
    "bg":          (0.102, 0.102, 0.122, 1),
    "surface":     (0.129, 0.129, 0.153, 1),
    "surface2":    (0.165, 0.165, 0.200, 1),
    "border":      (0.220, 0.220, 0.260, 1),
    "text":        (0.910, 0.910, 0.941, 1),
    "text_muted":  (0.533, 0.533, 0.627, 1),
    "text_faint":  (0.333, 0.333, 0.416, 1),
    "accent":      (0.800, 0.471, 0.361, 1),   # #cc785c
    "accent_h":    (0.851, 0.541, 0.431, 1),
    "user_bubble": (0.176, 0.176, 0.220, 1),
    "asst_bubble": (0.118, 0.118, 0.157, 1),
    "error":       (1.000, 0.420, 0.420, 1),
    "ok":          (0.247, 0.812, 0.557, 1),
    "white":       (1, 1, 1, 1),
}

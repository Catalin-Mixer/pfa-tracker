"""Colour themes: light, dark or custom colours. Each app (browser, desktop) has its own theme;
custom colour sets can be saved by name and are offered in both apps."""
import json
import re

HEX = re.compile(r"#[0-9a-fA-F]{6}")
THEMES = ("light", "dark", "custom")
CUSTOM_KEYS = ("accent", "background", "surface", "text")

LIGHT = {
    "text": "#1f2933", "muted": "#66727f", "line": "#e2e6eb", "background": "#f4f6f9", "surface": "#ffffff",
    "surface_alt": "#fafbfc", "accent": "#1d5fa8", "accent_soft": "#e8f0fa", "accent_text": "#ffffff",
    "bad": "#b42318", "warn": "#9a6700", "ok_bg": "#e8f5ec", "ok_border": "#b7dfc2", "err_bg": "#fdecea",
    "err_border": "#f5c2bd", "input_border": "#c9d0d8", "code_bg": "#eef1f4", "selection": "#2f84c9",
}
DARK = {
    "text": "#e4e8ee", "muted": "#9aa5b1", "line": "#2e3742", "background": "#14181d", "surface": "#1d232b",
    "surface_alt": "#232a33", "accent": "#5aa2ec", "accent_soft": "#1e3450", "accent_text": "#0b1521",
    "bad": "#f28b82", "warn": "#e6b450", "ok_bg": "#17301f", "ok_border": "#2d5a3b", "err_bg": "#3a1d1b",
    "err_border": "#6b2f2a", "input_border": "#3c4653", "code_bg": "#2a323c", "selection": "#2f6fb0",
}
DEFAULT_CUSTOM = {"accent": "#7a3fb0", "background": "#f6f3fa", "surface": "#ffffff", "text": "#221b2b"}


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _mix(a: str, b: str, t: float) -> str:
    """a blended towards b by t (0..1)."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def luminance(hex_color: str) -> float:
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def clean_colors(colors: dict) -> dict:
    """Checks custom colours: #rrggbb each, and text readable on the background and on cards."""
    out = {}
    for key in CUSTOM_KEYS:
        value = str(colors.get(key, "") or DEFAULT_CUSTOM[key]).strip()
        if not HEX.fullmatch(value):
            raise ValueError(f"the {key} colour must look like #1d5fa8")
        out[key] = value.lower()
    for surface in ("background", "surface"):
        if contrast(out["text"], out[surface]) < 4.5:
            raise ValueError(f"the text colour is too hard to read on the {surface} colour — pick a lighter or darker one")
    if contrast(out["accent"], out["surface"]) < 2.5:
        raise ValueError("the accent colour is too close to the surface colour")
    return out


def palette(theme: str, colors: dict | None = None) -> dict:
    """All colours of a theme. Custom colours start from light or dark (whichever suits the background)."""
    if theme == "dark":
        return dict(DARK)
    if theme != "custom":
        return dict(LIGHT)
    c = clean_colors(colors or {})
    dark = luminance(c["background"]) < 0.2
    base = dict(DARK if dark else LIGHT)
    base.update(c)
    base["muted"] = _mix(c["text"], c["background"], 0.4)
    base["line"] = _mix(c["surface"], c["text"], 0.12)
    base["surface_alt"] = _mix(c["surface"], c["text"], 0.03)
    base["accent_soft"] = _mix(c["surface"], c["accent"], 0.14)
    base["accent_text"] = "#ffffff" if contrast("#ffffff", c["accent"]) >= contrast("#000000", c["accent"]) else "#000000"
    base["input_border"] = _mix(c["surface"], c["text"], 0.25)
    base["code_bg"] = _mix(c["surface"], c["text"], 0.07)
    base["selection"] = c["accent"]
    return base


def from_settings(data: dict) -> tuple[str, dict]:
    """(theme name, custom colours) stored in a profile's settings.json, with safe fallbacks."""
    ui = data.get("ui") if isinstance(data, dict) and isinstance(data.get("ui"), dict) else {}
    theme = ui.get("theme") if ui.get("theme") in THEMES else "light"
    colors = ui.get("colors") if isinstance(ui.get("colors"), dict) else {}
    colors = {k: str(colors.get(k) or DEFAULT_CUSTOM[k]) for k in CUSTOM_KEYS}
    if theme == "custom":
        try:
            clean_colors(colors)
        except ValueError:
            theme = "light"
    return theme, colors



# Storage: ui-settings.json = {"apps": {"web": {...}, "desktop": {...}}, "saved_themes": [{"name", "colors"}]}

APPS = ("web", "desktop")
MAX_SAVED = 30
MAX_NAME = 40


def _read() -> dict:
    from . import config
    try:
        data = json.loads(config.ui_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    apps = data.get("apps") if isinstance(data.get("apps"), dict) else {}
    saved = data.get("saved_themes") if isinstance(data.get("saved_themes"), list) else []
    clean_saved = []
    for s in saved:
        if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("colors"), dict):
            try:
                clean_saved.append({"name": s["name"][:MAX_NAME], "colors": clean_colors(s["colors"])})
            except ValueError:
                continue
    return {"apps": {a: apps[a] for a in APPS if isinstance(apps.get(a), dict)}, "saved_themes": clean_saved}


def _write(data: dict) -> None:
    from . import config
    path = config.ui_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def app_theme(app: str) -> tuple[str, dict, str | None]:
    """(theme name, custom colours, name of the saved theme in use or None) of one app."""
    state = _read()["apps"].get(app, {})
    name, colors = from_settings({"ui": state})
    saved_name = state.get("saved") if isinstance(state.get("saved"), str) else None
    return name, colors, saved_name if name == "custom" else None


def set_app_theme(app: str, name: str, colors: dict | None, saved_name: str | None = None) -> None:
    if app not in APPS:
        raise ValueError("unknown app")
    if name not in THEMES:
        raise ValueError("choose light, dark or custom")
    data = _read()
    current = data["apps"].get(app, {})
    keep = from_settings({"ui": current})[1]
    clean = clean_colors(colors or {}) if name == "custom" else keep
    data["apps"][app] = {"theme": name, "colors": clean, **({"saved": saved_name} if saved_name and name == "custom" else {})}
    _write(data)


def saved_themes() -> list[dict]:
    return _read()["saved_themes"]


def clean_theme_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("give the theme a name")
    if len(name) > MAX_NAME:
        raise ValueError(f"a theme name can have at most {MAX_NAME} characters")
    if any(ord(c) < 32 for c in name):
        raise ValueError("the theme name is not readable")
    return name


def save_named(name: str, colors: dict) -> bool:
    """Saves a custom colour set under a name; returns True if it replaced one with the same name."""
    name = clean_theme_name(name)
    clean = clean_colors(colors)
    data = _read()
    replaced = False
    for s in data["saved_themes"]:
        if s["name"].lower() == name.lower():
            s["name"], s["colors"], replaced = name, clean, True
    if not replaced:
        if len(data["saved_themes"]) >= MAX_SAVED:
            raise ValueError(f"at most {MAX_SAVED} saved themes — delete one first")
        data["saved_themes"].append({"name": name, "colors": clean})
    _write(data)
    return replaced


def find_named(name: str) -> dict | None:
    return next((s for s in saved_themes() if s["name"].lower() == (name or "").strip().lower()), None)


def delete_named(name: str) -> None:
    data = _read()
    before = len(data["saved_themes"])
    data["saved_themes"] = [s for s in data["saved_themes"] if s["name"].lower() != (name or "").strip().lower()]
    if len(data["saved_themes"]) == before:
        raise ValueError("there is no saved theme with that name")
    for state in data["apps"].values():
        if str(state.get("saved", "")).lower() == (name or "").strip().lower():
            state.pop("saved", None)
    _write(data)

import time
import threading
import pystray
from PIL import Image, ImageDraw, ImageFont
import keyboard

# --- State ---
running = False
start_time = 0
elapsed = 0
update_thread = None
icon = None

# Pomodoro state
pomo_active = False
pomo_paused = False
pomo_remaining = 0
pomo_thread = None
pomo_lock = threading.Lock()

# Use system font
FONT_PATH = "C:\\Windows\\Fonts\\arial.ttf"

# ── Helpers ──────────────────────────────────────────────────

def format_time(sec):
    hrs = int(sec // 3600)
    mins = int((sec % 3600) // 60)
    secs = int(sec % 60)
    if hrs > 0:
        return f"{hrs:02}:{mins:02}"
    return f"{mins:02}:{secs:02}"


def create_icon_with_time(t, bg=(25, 25, 25), fg=(255, 255, 255), paused=False):
    """Create tray icon image with visible time text."""
    img = Image.new("RGB", (64, 64), bg)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(FONT_PATH, 26)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), t, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # Shift text up a bit when paused to make room for pause bars
    y_offset = -8 if paused else -4
    draw.text(((64 - w) / 2, (64 - h) / 2 + y_offset), t, fill=fg, font=font)
    if paused:
        # Draw two small pause bars at the bottom of the icon
        bar_w, bar_h = 6, 12
        gap = 6
        left_x = (64 - (bar_w * 2 + gap)) // 2
        top_y = 48
        draw.rectangle([left_x, top_y, left_x + bar_w, top_y + bar_h], fill=fg)
        draw.rectangle([left_x + bar_w + gap, top_y, left_x + bar_w * 2 + gap, top_y + bar_h], fill=fg)
    return img


# ── Windows toast notification (lightweight, no extra deps) ──

def _notify(title, msg):
    """Show a Windows 10/11 toast notification via PowerShell."""
    # Escape single-quotes for PowerShell
    title_esc = title.replace("'", "''")
    msg_esc = msg.replace("'", "''")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, "
        "ContentType = WindowsRuntime] | Out-Null; "
        f"$xml = '<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{title_esc}</text><text>{msg_esc}</text>"
        f"</binding></visual><audio src=\"ms-winsoundevent:Notification.Default\"/></toast>'; "
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        "$doc.LoadXml($xml); "
        "$notifier = [Windows.UI.Notifications.ToastNotificationManager]::"
        "CreateToastNotifier('Stopwatch'); "
        "$notifier.Show([Windows.UI.Notifications.ToastNotification]::new($doc))"
    )
    import subprocess
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-Command", ps],
        creationflags=0x08000000,   # CREATE_NO_WINDOW
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ── Stopwatch ────────────────────────────────────────────────

def update_time():
    global elapsed
    while running:
        elapsed = time.time() - start_time
        t = format_time(elapsed)
        try:
            icon.icon = create_icon_with_time(t)
            icon.title = f"Elapsed: {format_time(elapsed)} ({int(elapsed)}s)"
            icon.visible = True
        except Exception:
            pass
        time.sleep(1)


def toggle():
    """Unified Start / Pause for both stopwatch and pomodoro."""
    global running, start_time, elapsed, update_thread
    global pomo_paused

    # ── If a pomodoro is active or paused, handle that first ──
    with pomo_lock:
        if pomo_active and not pomo_paused:
            # Pause the running pomodoro
            pomo_paused = True
            t = format_time(pomo_remaining)
            try:
                icon.icon = create_icon_with_time(
                    t, bg=(180, 120, 0), fg=(255, 255, 255), paused=True
                )
                icon.title = f"Pomodoro Paused: {t}"
            except Exception:
                pass
            return
        if pomo_active and pomo_paused:
            # Resume the paused pomodoro
            pomo_paused = False
            return

    # ── Otherwise, handle the normal stopwatch ──
    if not running:
        running = True
        start_time = time.time() - elapsed
        update_thread = threading.Thread(target=update_time, daemon=True)
        update_thread.start()
    else:
        running = False
        elapsed = time.time() - start_time
        # Show paused state on the icon
        t = format_time(elapsed)
        try:
            icon.icon = create_icon_with_time(
                t, bg=(180, 120, 0), fg=(255, 255, 255), paused=True
            )
            icon.title = f"Paused: {t} ({int(elapsed)}s)"
        except Exception:
            pass


# ── Pomodoro ─────────────────────────────────────────────────

def _pomo_tick():
    """Background thread that counts down the pomodoro timer."""
    global pomo_active, pomo_paused, pomo_remaining
    end_time = time.time() + pomo_remaining
    while True:
        with pomo_lock:
            if not pomo_active:
                return
            if pomo_paused:
                # While paused, just sleep and keep waiting
                pass
            else:
                # Actively counting down
                pomo_remaining = max(0, end_time - time.time())
                t = format_time(pomo_remaining)
                try:
                    icon.icon = create_icon_with_time(t, bg=(140, 30, 30), fg=(255, 255, 255))
                    mins_left = int(pomo_remaining // 60)
                    secs_left = int(pomo_remaining % 60)
                    icon.title = f"Pomodoro: {mins_left}m {secs_left}s remaining"
                except Exception:
                    pass
                if pomo_remaining <= 0:
                    break

        # If we just transitioned from paused→running, recalculate end_time
        with pomo_lock:
            was_paused = pomo_paused
        if not was_paused:
            # Continuously re-anchor end_time so pause doesn't eat time
            pass
        else:
            # Paused: push end_time forward so no time is lost
            end_time = time.time() + pomo_remaining

        time.sleep(1)

    # Timer done
    with pomo_lock:
        pomo_active = False
        pomo_paused = False
    _notify("Pomodoro Complete!", "Time's up! Take a break.")
    # Reset icon to idle
    try:
        icon.icon = create_icon_with_time("00:00")
        icon.title = "Stopwatch"
    except Exception:
        pass


def start_pomo(minutes):
    """Start a pomodoro countdown of *minutes* minutes."""
    global pomo_active, pomo_paused, pomo_remaining, pomo_thread, running, elapsed

    # Stop the stopwatch if it's running and reset it
    if running:
        running = False
    elapsed = 0

    # Cancel any existing pomodoro
    with pomo_lock:
        pomo_active = False
        pomo_paused = False
    if pomo_thread and pomo_thread.is_alive():
        pomo_thread.join(timeout=2)

    with pomo_lock:
        pomo_active = True
        pomo_paused = False
        pomo_remaining = minutes * 60

    pomo_thread = threading.Thread(target=_pomo_tick, daemon=True)
    pomo_thread.start()


def cancel_pomo(icon_, item):
    """Cancel the running pomodoro."""
    global pomo_active, pomo_paused
    with pomo_lock:
        pomo_active = False
        pomo_paused = False
    try:
        icon_.icon = create_icon_with_time("00:00")
        icon_.title = "Stopwatch"
    except Exception:
        pass


def _custom_pomo(icon_, item):
    """Prompt the user for a custom duration via a tiny Tk dialog."""
    def _ask():
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        mins = simpledialog.askinteger(
            "Pomodoro Timer",
            "Enter duration in minutes:",
            minvalue=1,
            maxvalue=600,
            parent=root,
        )
        root.destroy()
        if mins:
            start_pomo(mins)
    # Run in a thread so we don't block pystray
    threading.Thread(target=_ask, daemon=True).start()


# ── Menu ─────────────────────────────────────────────────────

pomo_submenu = pystray.Menu(
    pystray.MenuItem("5 min",   lambda icon_, item: start_pomo(5)),
    pystray.MenuItem("10 min",  lambda icon_, item: start_pomo(10)),
    pystray.MenuItem("15 min",  lambda icon_, item: start_pomo(15)),
    pystray.MenuItem("25 min",  lambda icon_, item: start_pomo(25)),
    pystray.MenuItem("45 min",  lambda icon_, item: start_pomo(45)),
    pystray.MenuItem("60 min",  lambda icon_, item: start_pomo(60)),
    pystray.Menu.SEPARATOR,
    pystray.MenuItem("Custom…", _custom_pomo),
    pystray.Menu.SEPARATOR,
    pystray.MenuItem("Cancel Timer", cancel_pomo),
)

menu = pystray.Menu(
    pystray.MenuItem("Start / Pause", lambda icon_, item: toggle()),
    pystray.MenuItem("Pomodoro", pomo_submenu),
    pystray.MenuItem("Quit", lambda icon_, item: (
        setattr(icon_, '_pomo_quit', True),
        cancel_pomo(icon_, item),
        icon_.stop(),
    )),
)

# ── Entry point ──────────────────────────────────────────────

icon = pystray.Icon("Stopwatch", create_icon_with_time("00:00"), "Stopwatch", menu)

keyboard.add_hotkey("windows+alt+s", toggle)

icon.run()

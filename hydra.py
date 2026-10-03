import json
import math
import platform
import random
import re
import subprocess
import sys
import threading
import time
import datetime as dt
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

import requests

BASE = Path(__file__).parent
CONFIG = BASE / "config.json"
DATA = BASE / "data.json"
LOGFILE = BASE / "hydra.log"
LOCK = threading.Lock()
NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")
TITLE = "❁Hydra❁"

BLUE = "#b7dbee"
DOT = "#eef8fd"
LACE = "#f2aebb"
PINK = "#f8cdd5"
PINK_LIGHT = "#fbdde3"
CORAL = "#c9504f"
RED = "#e0404a"
BLUE_BTN = "#7fb9d6"
EMPTY = "#fdebef"

DEFAULT_CONFIG = {
    "friend_name": "Sam",
    "goal_ml": 2000,
    "interval_min": 1,
    "active_hours": [9, 23],
    "tone": "playful, short, like a supportive friend, never naggy",
    "about_friend": "Studies late and forgets to drink water. Loves coding puns.",
    "model": "llama3.2:3b",
    "ollama_url": "http://localhost:11434/api/generate",
    "flourishes": ["ꫂ❁❁𔓘", "❁✿❀", "❁✿❀", "✿❀✿", "❁✿❁"],
}


def log(*parts):
    line = " ".join(str(p) for p in parts)
    try:
        if sys.stdout:
            print(line)
    except Exception:
        pass
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config():
    if not CONFIG.exists():
        CONFIG.write_text(
            json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return cfg


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def today():
    return dt.date.today().isoformat()


def load_data():
    try:
        d = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    except Exception:
        d = {}
    if d.get("date") != today():
        d = {"date": today(), "consumed": 0, "log": []}
    return d


def save_data(d):
    DATA.write_text(json.dumps(d, indent=2), encoding="utf-8")


def read_data():
    with LOCK:
        return load_data()


def change(ml):
    with LOCK:
        d = load_data()
        before = d["consumed"]
        d["consumed"] = max(0, before + ml)
        d["log"].append({"time": dt.datetime.now().strftime("%H:%M"), "ml": ml})
        save_data(d)
        return before, d["consumed"]


def set_consumed(value):
    with LOCK:
        d = load_data()
        diff = value - d["consumed"]
        if diff != 0:
            d["log"].append({"time": dt.datetime.now().strftime("%H:%M"), "ml": diff})
        d["consumed"] = value
        save_data(d)


def windows_toast(title, msg):
    from xml.sax.saxutils import escape
    t = escape(title).replace("'", "''")
    m = escape(msg).replace("'", "''")
    ps = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $x.GetElementsByTagName("text")
$n.Item(0).AppendChild($x.CreateTextNode('{t}')) | Out-Null
$n.Item(1).AppendChild($x.CreateTextNode('{m}')) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($x)
$id = "{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe"
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($toast)
'''
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=True,
        creationflags=NO_WINDOW,
    )


def notify(title, msg):
    log(f"[{dt.datetime.now():%H:%M}] {title}: {msg}")
    system = platform.system()
    try:
        if system == "Darwin":
            safe = msg.replace('"', "'")
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{safe}" with title "{title}"'],
                check=True,
            )
        elif system == "Linux":
            subprocess.run(["notify-send", title, msg], check=True)
        elif system == "Windows":
            windows_toast(title, msg)
    except Exception as e:
        log("desktop notification failed:", e)


def decorate(cfg, text):
    options = cfg.get("flourishes") or [""]
    return f"{text} {random.choice(options)}".strip()


def fallback(cfg, remaining):
    return decorate(cfg, f"{cfg['friend_name']}, {remaining} ml to go. Grab some water!")


def ai_message(cfg, d):
    remaining = cfg["goal_ml"] - d["consumed"]
    now = dt.datetime.now().strftime("%H:%M")
    prompt = (
        f"You write one hydration reminder for {cfg['friend_name']}.\n"
        f"About them: {cfg['about_friend']}\n"
        f"Tone: {cfg['tone']}\n"
        f"Time now: {now}. They have had {d['consumed']} ml of "
        f"{cfg['goal_ml']} ml today, {remaining} ml left.\n"
        "Rules: max 20 words, no emoji, no symbols, no quotes, no hashtags. "
        "Output only the message."
    )
    try:
        r = requests.post(
            cfg["ollama_url"],
            json={
                "model": cfg["model"],
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.9},
            },
            timeout=90,
        )
        r.raise_for_status()
        text = EMOJI.sub("", r.json()["response"]).strip().strip('"').strip()
        if not text:
            return fallback(cfg, remaining)
        return decorate(cfg, text[:200])
    except Exception as e:
        log("Ollama unavailable, using fallback:", e)
        return fallback(cfg, remaining)


def reminder_loop(stop):
    cfg = load_config()
    next_due = time.time() + cfg["interval_min"] * 60
    while not stop.is_set():
        try:
            cfg = load_config()
            d = read_data()
            lo, hi = cfg["active_hours"]
            hour = dt.datetime.now().hour
            if (
                lo <= hour < hi
                and d["consumed"] < cfg["goal_ml"]
                and time.time() >= next_due
            ):
                notify(TITLE, ai_message(cfg, d))
                next_due = time.time() + cfg["interval_min"] * 60
        except Exception as e:
            log("loop error:", e)
        stop.wait(30)


def flat(pts):
    return [c for p in pts for c in p]


def heart_pts(cx, cy, s, n=110):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = (
            13 * math.cos(t)
            - 5 * math.cos(2 * t)
            - 2 * math.cos(3 * t)
            - math.cos(4 * t)
        )
        pts.append((cx + x * s, cy - y * s))
    return pts


def rrect_pts(cx, cy, w, h, r, angle=0):
    base = []
    corners = [
        (w / 2 - r, h / 2 - r, 0),
        (-(w / 2 - r), h / 2 - r, 90),
        (-(w / 2 - r), -(h / 2 - r), 180),
        (w / 2 - r, -(h / 2 - r), 270),
    ]
    for ox, oy, start in corners:
        for i in range(0, 91, 10):
            a = math.radians(start + i)
            base.append((ox + r * math.cos(a), oy + r * math.sin(a)))
    ang = math.radians(angle)
    out = []
    for x, y in base:
        out.append(cx + x * math.cos(ang) - y * math.sin(ang))
        out.append(cy + x * math.sin(ang) + y * math.cos(ang))
    return out


def pick_font(root):
    avail = set(tkfont.families(root))
    for name in (
        "Segoe Script",
        "Lucida Handwriting",
        "Brush Script MT",
        "Ink Free",
        "Segoe Print",
        "Comic Sans MS",
    ):
        if name in avail:
            return name
    return "Arial"


class App:
    def __init__(self, root):
        self.root = root
        self.goal = 2000
        self.stop = threading.Event()
        self.thread = None
        self.entries = []
        self.mode = "setup"

        root.title("Hydra")
        root.geometry("360x600")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.iconify)

        self.font = pick_font(root)
        self.cv = tk.Canvas(root, width=360, height=600, bg=BLUE, highlightthickness=0)
        self.cv.pack()

        self.draw_dots()
        root.bind(
            "<Return>",
            lambda e: self.root.after(20, self.start) if self.mode == "setup" else None,
        )
        self.draw_setup()

    def draw_dots(self):
        for row, y in enumerate(range(15, 640, 40)):
            off = 20 if row % 2 else 0
            for x in range(off, 400, 40):
                self.cv.create_oval(
                    x - 5.5, y - 5.5, x + 5.5, y + 5.5,
                    fill=DOT, outline="", tags="bg",
                )

    def clear(self):
        self.cv.delete("ui")
        self.cv.delete("dyn")
        self.cv.delete("err")
        for e in self.entries:
            e.destroy()
        self.entries = []

    def pill(self, x, y, w, h, text, command, fill):
        poly = self.cv.create_polygon(
            rrect_pts(x, y, w, h, h / 2), fill=fill, outline="", tags="ui"
        )
        label = self.cv.create_text(
            x, y, text=text, fill="white", font=(self.font, 15), tags="ui"
        )
        for item in (poly, label):
            self.cv.tag_bind(item, "<Button-1>", lambda e: self.root.after(20, command))
            self.cv.tag_bind(item, "<Enter>", lambda e: self.cv.config(cursor="hand2"))
            self.cv.tag_bind(item, "<Leave>", lambda e: self.cv.config(cursor=""))

    def entry(self, x, y, var):
        e = tk.Entry(
            self.cv,
            textvariable=var,
            width=9,
            justify="center",
            font=(self.font, 14),
            bg="#fff4f6",
            fg=CORAL,
            insertbackground=CORAL,
            relief="flat",
            highlightthickness=2,
            highlightbackground=LACE,
            highlightcolor=CORAL,
        )
        self.cv.create_window(x, y, window=e, tags="ui")
        self.entries.append(e)
        return e

    def clip(self, cx, cy, kind):
        ang = 75
        length = 52
        self.cv.create_polygon(
            rrect_pts(cx, cy, length, 12, 5, ang),
            fill="#f6b4c6", outline="#e592ab", width=1, tags="ui",
        )
        tx = cx - (length / 2) * math.cos(math.radians(ang))
        ty = cy - (length / 2) * math.sin(math.radians(ang))
        if kind == "heart":
            self.cv.create_polygon(
                flat(heart_pts(tx, ty, 0.8, 40)),
                fill=RED, outline="#ffffff", width=2, tags="ui",
            )
        else:
            self.cv.create_oval(
                tx - 13, ty - 10, tx + 13, ty + 10,
                fill="#ffffff", outline="#e8c9d0", width=1, tags="ui",
            )
            self.cv.create_rectangle(tx - 2, ty - 6, tx + 2, ty + 6, fill=RED, outline="", tags="ui")
            self.cv.create_rectangle(tx - 6, ty - 2, tx + 6, ty + 2, fill=RED, outline="", tags="ui")

    def bandaid(self, cx, cy, ang):
        self.cv.create_polygon(
            rrect_pts(cx, cy, 80, 30, 14, ang),
            fill="#f9d9d6", outline="#e5b0ae", width=2, tags="ui",
        )
        self.cv.create_polygon(
            rrect_pts(cx, cy, 30, 24, 3, ang),
            fill="#fdf1f0", outline="#ecc6c4", width=1, tags="ui",
        )
        self.cv.create_polygon(
            flat(heart_pts(cx, cy, 0.6, 40)), fill="#c8403f", outline="", tags="ui"
        )

    def draw_frame(self):
        name = load_config()["friend_name"]
        self.cv.create_text(
            180, 95, text=f"drink up, {name}", fill=CORAL,
            font=(self.font, 16), tags="ui",
        )

        cx, cy, s = 180, 268, 10.0
        ring = heart_pts(cx, cy, s * 1.03, 110)
        for x, y in ring:
            self.cv.create_oval(x - 8, y - 8, x + 8, y + 8, fill=LACE, outline="", tags="ui")
        for x, y in ring:
            self.cv.create_oval(
                x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill="#fde9ee", outline="", tags="ui"
            )
        self.cv.create_polygon(
            flat(heart_pts(cx, cy, s * 0.98)),
            fill=PINK, outline=LACE, width=3, tags="ui",
        )
        self.cv.create_polygon(
            flat(heart_pts(cx, cy, s * 0.9)), fill=PINK_LIGHT, outline="", tags="ui"
        )

        self.clip(250, 200, "heart")
        self.clip(284, 206, "cross")
        self.bandaid(108, 375, -40)

    def draw_setup(self):
        self.clear()
        self.mode = "setup"
        self.draw_frame()

        cfg = load_config()
        d = read_data()

        self.cv.create_text(180, 190, text="hydra", fill=CORAL, font=(self.font, 30), tags="ui")
        self.cv.create_text(180, 232, text="daily goal (ml)", fill=CORAL, font=(self.font, 11), tags="ui")
        self.goal_var = tk.StringVar(value=str(cfg["goal_ml"]))
        first = self.entry(180, 258, self.goal_var)

        self.cv.create_text(180, 297, text="had so far (ml)", fill=CORAL, font=(self.font, 11), tags="ui")
        self.had_var = tk.StringVar(value=str(d["consumed"]))
        self.entry(180, 325, self.had_var)

        self.pill(180, 490, 170, 46, "start", self.start, CORAL)
        self.quit_link()
        first.focus_set()

    def draw_main(self):
        self.clear()
        self.mode = "main"
        self.draw_frame()

        self.cv.create_text(180, 190, text="hydra", fill=CORAL, font=(self.font, 30), tags="ui")
        self.pill(105, 490, 120, 46, "- 250", lambda: self.press(-250), BLUE_BTN)
        self.pill(255, 490, 120, 46, "+ 250", lambda: self.press(250), CORAL)
        self.quit_link()
        self.refresh()

    def quit_link(self):
        item = self.cv.create_text(
            180, 565, text="quit hydra", fill="#3c7f9a",
            font=(self.font, 11, "underline"), tags="ui",
        )
        self.cv.tag_bind(item, "<Button-1>", lambda e: self.root.after(20, self.quit))
        self.cv.tag_bind(item, "<Enter>", lambda e: self.cv.config(cursor="hand2"))
        self.cv.tag_bind(item, "<Leave>", lambda e: self.cv.config(cursor=""))

    def start(self):
        if self.mode != "setup":
            return
        self.cv.delete("err")
        try:
            goal = int(self.goal_var.get().strip())
            had = int(self.had_var.get().strip())
            if goal < 1 or had < 0:
                raise ValueError
        except ValueError:
            self.cv.create_text(
                180, 455, text="whole numbers only, goal at least 1",
                fill=RED, font=(self.font, 10), tags="err",
            )
            return

        cfg = load_config()
        cfg["goal_ml"] = goal
        save_config(cfg)
        set_consumed(had)
        self.goal = goal

        self.draw_main()

        if self.thread is None:
            self.thread = threading.Thread(
                target=reminder_loop, args=(self.stop,), daemon=True
            )
            self.thread.start()
        self.root.after(60000, self.tick)

    def refresh(self):
        d = read_data()
        c = d["consumed"]
        self.cv.delete("dyn")
        self.cv.create_text(
            180, 245, text=f"{c} / {self.goal} ml", fill=CORAL,
            font=(self.font, 18), tags="dyn",
        )
        filled = int(round(10 * min(c / self.goal, 1)))
        for i in range(10):
            x = 180 + (i - 4.5) * 22
            self.cv.create_polygon(
                flat(heart_pts(x, 290, 0.55, 40)),
                fill=RED if i < filled else EMPTY,
                outline=LACE, width=1, tags="dyn",
            )
        remaining = self.goal - c
        self.cv.create_text(
            180, 335,
            text=f"{remaining} ml to go" if remaining > 0 else "goal reached",
            fill=CORAL, font=(self.font, 12), tags="dyn",
        )

    def tick(self):
        try:
            self.goal = load_config()["goal_ml"]
        except Exception:
            pass
        if self.mode == "main":
            self.refresh()
        self.root.after(60000, self.tick)

    def press(self, ml):
        before, after = change(ml)
        self.refresh()
        if before < self.goal <= after:
            cfg = load_config()
            threading.Thread(
                target=notify,
                args=(TITLE, decorate(cfg, f"Goal reached, {cfg['friend_name']}! Nice work.")),
                daemon=True,
            ).start()

    def quit(self):
        self.stop.set()
        self.root.destroy()


def run_gui():
    root = tk.Tk()
    App(root)
    root.mainloop()


def status():
    cfg = load_config()
    d = read_data()
    left = max(0, cfg["goal_ml"] - d["consumed"])
    print(f"{d['consumed']} / {cfg['goal_ml']} ml  ({left} ml left)")
    for e in d["log"]:
        print(f"  {e['time']}  {e['ml']:+d} ml")


def add(ml):
    cfg = load_config()
    before, after = change(ml)
    print(f"{after} / {cfg['goal_ml']} ml")


def test():
    cfg = load_config()
    notify(TITLE, ai_message(cfg, read_data()))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gui"

    if cmd == "gui":
        run_gui()
    elif cmd == "status":
        status()
    elif cmd == "add":
        add(int(sys.argv[2]) if len(sys.argv) > 2 else 250)
    elif cmd == "test":
        test()
    else:
        print("usage: python hydra.py [gui | status | add <ml> | test]")
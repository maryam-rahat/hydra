import json
import platform
import subprocess
import sys
import threading
import time
import datetime as dt
import tkinter as tk
from tkinter import ttk
from pathlib import Path

import requests

BASE = Path(__file__).parent
CONFIG = BASE / "config.json"
DATA = BASE / "data.json"
LOGFILE = BASE / "hydra.log"
LOCK = threading.Lock()
NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0

DEFAULT_CONFIG = {
    "friend_name": "Alex",
    "goal_ml": 2000,
    "interval_min": 60,
    "active_hours": [0, 24],
    "tone": "playful, short, like a supportive friend, never naggy",
    "about_friend": "Studies late and forgets to drink water. Loves tea and football.",
    "model": "llama3.2:3b",
    "ollama_url": "http://localhost:11434/api/generate",
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
        CONFIG.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return cfg


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


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


def fallback(remaining):
    return f"{remaining} ml to go. Grab some water! 💧"


def ai_message(cfg, d):
    remaining = cfg["goal_ml"] - d["consumed"]
    now = dt.datetime.now().strftime("%H:%M")
    prompt = (
        f"You write one hydration reminder for {cfg['friend_name']}.\n"
        f"About them: {cfg['about_friend']}\n"
        f"Tone: {cfg['tone']}\n"
        f"Time now: {now}. They have had {d['consumed']} ml of "
        f"{cfg['goal_ml']} ml today, {remaining} ml left.\n"
        "Rules: max 20 words, one emoji, no quotes, no hashtags. "
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
        text = r.json()["response"].strip().strip('"')
        return text[:200] or fallback(remaining)
    except Exception as e:
        log("Ollama unavailable, using fallback:", e)
        return fallback(remaining)


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
                notify("💧 Hydra", ai_message(cfg, d))
                next_due = time.time() + cfg["interval_min"] * 60
        except Exception as e:
            log("loop error:", e)
        stop.wait(30)


class App:
    def __init__(self, root):
        self.root = root
        self.goal = 2000
        self.stop = threading.Event()
        self.thread = None

        root.title("Hydra")
        root.geometry("300x340")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.iconify)

        self.setup_frame = ttk.Frame(root, padding=20)
        self.main_frame = ttk.Frame(root, padding=20)
        self.build_setup()
        self.build_main()
        self.setup_frame.pack(fill="both", expand=True)

    def build_setup(self):
        cfg = load_config()
        d = read_data()
        f = self.setup_frame

        ttk.Label(f, text="HYDRA", font=("Arial", 20, "bold")).pack(pady=(0, 15))

        ttk.Label(f, text="How much do you want to drink today (ml)?").pack(anchor="w")
        self.goal_var = tk.StringVar(value=str(cfg["goal_ml"]))
        goal_entry = ttk.Entry(f, textvariable=self.goal_var)
        goal_entry.pack(fill="x", pady=(2, 12))

        ttk.Label(f, text="How much have you had so far (ml)?").pack(anchor="w")
        self.had_var = tk.StringVar(value=str(d["consumed"]))
        had_entry = ttk.Entry(f, textvariable=self.had_var)
        had_entry.pack(fill="x", pady=(2, 12))

        self.error_label = ttk.Label(f, text="", foreground="red")
        self.error_label.pack()

        ttk.Button(f, text="Start", command=self.start).pack(fill="x", pady=10)
        self.root.bind("<Return>", lambda e: self.start() if self.setup_frame.winfo_ismapped() else None)
        goal_entry.focus()

    def build_main(self):
        f = self.main_frame

        ttk.Label(f, text="HYDRA", font=("Arial", 20, "bold")).pack(pady=(0, 10))

        self.amount_label = ttk.Label(f, text="", font=("Arial", 24, "bold"))
        self.amount_label.pack(pady=5)

        self.bar = ttk.Progressbar(f, length=240, mode="determinate")
        self.bar.pack(pady=10)

        self.remaining_label = ttk.Label(f, text="")
        self.remaining_label.pack(pady=5)

        row = ttk.Frame(f)
        row.pack(pady=15, fill="x")
        ttk.Button(row, text="-250", command=lambda: self.press(-250)).pack(
            side="left", expand=True, fill="x", padx=(0, 5)
        )
        ttk.Button(row, text="+250", command=lambda: self.press(250)).pack(
            side="left", expand=True, fill="x", padx=(5, 0)
        )

        ttk.Button(f, text="Quit Hydra", command=self.quit).pack(pady=5)

    def start(self):
        try:
            goal = int(self.goal_var.get().strip())
            had = int(self.had_var.get().strip())
            if goal < 1 or had < 0:
                raise ValueError
        except ValueError:
            self.error_label.config(text="Enter whole numbers (goal at least 1).")
            return

        cfg = load_config()
        cfg["goal_ml"] = goal
        save_config(cfg)
        set_consumed(had)

        self.goal = goal
        self.setup_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)
        self.refresh()

        if self.thread is None:
            self.thread = threading.Thread(
                target=reminder_loop, args=(self.stop,), daemon=True
            )
            self.thread.start()
        self.root.after(60000, self.tick)

    def refresh(self):
        d = read_data()
        consumed = d["consumed"]
        self.amount_label.config(text=f"{consumed} / {self.goal} ml")
        self.bar.config(maximum=self.goal, value=min(consumed, self.goal))
        remaining = self.goal - consumed
        self.remaining_label.config(
            text=f"{remaining} ml remaining" if remaining > 0 else "🎉 Daily goal reached!"
        )

    def tick(self):
        try:
            self.goal = load_config()["goal_ml"]
        except Exception:
            pass
        self.refresh()
        self.root.after(60000, self.tick)

    def press(self, ml):
        before, after = change(ml)
        self.refresh()
        if before < self.goal <= after:
            cfg = load_config()
            threading.Thread(
                target=notify,
                args=("🎉 Hydra", f"Goal reached, {cfg['friend_name']}! Nice work."),
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
    notify("💧 Hydra", ai_message(cfg, read_data()))


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
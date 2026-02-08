# -*- coding: utf-8 -*-

import warnings
import os
import json
import threading
import queue
import subprocess
import math
import time
import tkinter as tk
from tkinter import font as tkfont
from collections import deque

# third-party
import customtkinter as ctk
import matplotlib
matplotlib.use('TkAgg')  # Explicitly set backend
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from pystray import Icon, MenuItem, Menu
from PIL import Image

APP_NAME = "PingWidgetModern"
CONFIG_FILE = os.path.expanduser(f"~/.{APP_NAME}.json")
DEFAULT_CONFIG = {
    "host": "google.com",
    "refresh_sec": 2.0,
    "window_size": 320,
    "stay_on_top": True,
    "win_x": None,
    "win_y": None,
    "auto_hide_sec": 0.0  # 0 = never; else seconds of inactivity to enter Zen
}

HISTORY_LENGTH_FULL = 5
HISTORY_LENGTH_ZEN  = 10

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(data)
            return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

def ping_once_ms(host, timeout_ms=1000):
    param = "-n" if os.name == "nt" else "-c"
    timeout_param = "-w" if os.name == "nt" else "-W"
    
    # Prepare startupinfo for Windows
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    
    # Convert timeout value for Linux (seconds vs milliseconds)
    timeout_val = str(timeout_ms if os.name == "nt" else timeout_ms // 1000)
    
    try:
        completed = subprocess.run(
            ["ping", param, "1", timeout_param, timeout_val, host],
            capture_output=True,
            text=True,
            timeout=(timeout_ms / 1000.0) + 1.0,
            startupinfo=startupinfo
        )
        if completed.returncode != 0:
            return float("inf")
        out = completed.stdout.lower()
        idx = out.find("time=")
        if idx != -1:
            tail = out[idx + len("time="):]
            num = ""
            for ch in tail:
                if ch.isdigit() or ch == ".":
                    num += ch
                elif ch in " m<":
                    break
            if num:
                return float(num)
            if tail.startswith("<1ms"):
                return 1.0
    except Exception:
        pass
    return float("inf")

class ModernPingWidget(ctk.CTk):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg  # ✅ FIXED: Store config
        self._current_host = cfg["host"]
        self.title(APP_NAME)
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        self._resize_start_geo = None
        self._resize_anchor = None

        # --- state ---
        self._q = queue.Queue()
        self._stop_evt = threading.Event()
        self.history = []
        self.recent_minute = deque()  # (timestamp, ms-or-None)
        self._zen = False
        self._idle_after_id = None
        self._drag_off = (0, 0)
        self._pre_zen_geometry = None
        self._zen_bg_color = "#1f1f1f"

        # --- geometry & position ---
        side = int(cfg.get("window_size", 320))
        geo = f"{side}x{side+100}"
        if cfg.get("win_x") is not None and cfg.get("win_y") is not None:
            geo += f"+{int(cfg['win_x'])}+{int(cfg['win_y'])}"
        self.geometry(geo)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # --- vars ---
        self.host_var = ctk.StringVar(value=cfg["host"])
        self.latency_var = ctk.StringVar(value="- ms")
        self.avg_var = ctk.StringVar(value="-")

        # ===== Header =====
        self.top_frame = ctk.CTkFrame(self)
        self.top_frame.pack(side="top", fill="x", padx=4, pady=4)
        self.status_indicator = ctk.CTkLabel(self.top_frame, text="●", font=("Segoe UI", 20, "bold"))
        self.status_indicator.pack(side="left", padx=(6, 0))

        self.host_box = ctk.CTkEntry(self.top_frame, textvariable=self.host_var, width=180)
        self.host_box.pack(side="left", padx=(8, 6), pady=8)
        self.save_btn = ctk.CTkButton(self.top_frame, text="Save", width=64, command=self.save_host)
        self.save_btn.pack(side="left", padx=(0, 6))
        self.settings_btn = ctk.CTkButton(self.top_frame, text="Settings", width=86, command=self.open_settings)
        self.settings_btn.pack(side="right", padx=(6, 8))
        self.hide_btn = ctk.CTkButton(self.top_frame, text="Hide", width=64, command=self._enter_zen)
        self.hide_btn.pack(side="right", padx=(6, 6))

        # ===== Center row: AVG/MIN (far left) + big ping centered =====
        self.center_row = ctk.CTkFrame(self, fg_color="transparent")
        self.center_row.pack(side="top", fill="x", padx=10, pady=(2, 0))
        # left panel
        self.avg_panel = ctk.CTkFrame(self.center_row, width=90)
        self.avg_panel.pack(side="left", padx=(0, 10))
        self.avg_caption = ctk.CTkLabel(self.avg_panel, text="AVG/MIN", font=("Segoe UI", 11, "bold"))
        self.avg_caption.pack(anchor="w")
        self.avg_label = ctk.CTkLabel(self.avg_panel, textvariable=self.avg_var, font=("Segoe UI", 40, "bold"))
        self.avg_label.pack(anchor="w")
        self.loss_var = ctk.StringVar(value="0% loss")
        self.loss_label = ctk.CTkLabel(self.avg_panel, textvariable=self.loss_var, font=("Segoe UI", 11))
        self.loss_label.pack(anchor="w")

        # center ping – expand so it stays centered
        self.latency_label = ctk.CTkLabel(self.center_row, textvariable=self.latency_var, font=("Segoe UI", 88, "bold"))
        self.latency_label.pack(side="left", expand=True)

        # ===== Bar chart (main) =====
        self.fig = Figure(figsize=(3.2, 1.0), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.axis("off")
        self.canvas_frame = ctk.CTkFrame(self)
        self.canvas_frame.pack(side="top", fill="both", expand=True, padx=10, pady=(4, 10))
        self.spark_canvas = FigureCanvasTkAgg(self.fig, master=self.canvas_frame)
        self.spark_canvas_widget = self.spark_canvas.get_tk_widget()
        self.spark_canvas_widget.pack(fill="both", expand=True)

        # ===== Zen label (only number) + resize grip =====
        self.zen_label = ctk.CTkLabel(self, textvariable=self.latency_var, text_color="#000000")
        self.zen_font = ctk.CTkFont(family="Segoe UI", size=44, weight="bold")
        self.zen_label.configure(font=self.zen_font)

        self.zen_grip = tk.Frame(self, width=14, height=14, cursor="bottom_right_corner", bg="#000000")
        
        # Get actual default color safely
        temp_frame = ctk.CTkFrame(self)
        self._normal_fg_color = temp_frame.cget("fg_color")
        temp_frame.destroy()

        # Zen AVG/MIN widgets (create once up front)
        self.zen_avg_caption = ctk.CTkLabel(self, text="AVG/MIN", text_color="#000000")
        self.zen_avg_font_caption = ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
        self.zen_avg_caption.configure(font=self.zen_avg_font_caption)

        self.zen_avg_label = ctk.CTkLabel(self, textvariable=self.avg_var, text_color="#000000")
        self.zen_avg_font_value = ctk.CTkFont(family="Segoe UI", size=32, weight="bold")
        self.zen_avg_label.configure(font=self.zen_avg_font_value)

        # Tray (optional)
        self.icon_image = Image.new("RGB", (64, 64), (120, 180, 240))
        self.tray_menu = Menu(MenuItem('Show', self.show_window), MenuItem('Quit', self.tray_quit))
        self._tray_icon = Icon(APP_NAME, self.icon_image, APP_NAME, self.tray_menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

        # Always on top & resizable
        self.attributes("-topmost", cfg.get("stay_on_top", True))
        self.resizable(True, True)
        self.update_idletasks()
        self._apply_minimum_size()

        # Events
        self.bind("<Configure>", self._on_configure)
        self.bind_all("<Double-Button-1>", self._wake_from_double_click, add="+")
        for ev in ("<Motion>", "<Any-Button>", "<KeyPress>"):
            self.bind_all(ev, self._reset_idle_timer, add="+")
        
        self.zen_grip.bind("<ButtonPress-1>", self._zen_resize_start)
        self.zen_grip.bind("<B1-Motion>", self._zen_resize_drag)
        self.bind_all("<Control-MouseWheel>", self._zen_wheel_resize, add="+")

        # Start ping worker & UI loop
        threading.Thread(target=self.ping_loop, daemon=True).start()
        self.after(120, self.drain_queue)

        # Kick idle timer
        self._reset_idle_timer()

    # ------------- helpers / colors -------------
    def _get_bg(self): return "#1f1f1f"
    
    def _color_for_value(self, v):
        if v is None: return "#8A8A8A"
        if v < 100:   return "#00A000"
        if v < 150:   return "#66CC66"
        if v < 250:   return "#FFA500"
        if v < 450:   return "#FF6666"
        return "#C00000"

    def _color_for_bg(self, bg_color):
        try:
            rgb = self.winfo_rgb(bg_color)
            brightness = sum(rgb) / (3 * 65535)
            return "#000000" if brightness > 0.5 else "#FFFFFF"
        except:
            return "#000000"

    # ------------- sizing / position -------------
    def _apply_minimum_size(self):
        if self._zen:
            self.minsize(180, 180)
        else:
            min_w = max(self.top_frame.winfo_reqwidth() + 16, 360)
            self.minsize(min_w, 230)

    def _on_configure(self, event):
        if event.widget is self and not self._zen:
            self.cfg["window_size"] = max(220, min(self.winfo_width(), self.winfo_height()))
            self.cfg["win_x"] = self.winfo_x()
            self.cfg["win_y"] = self.winfo_y()
            if hasattr(self, '_save_after_id'):
                self.after_cancel(self._save_after_id)
            self._save_after_id = self.after(500, lambda: save_config(self.cfg))

    def update_latency_color(self, ms):
        col = self._color_for_value(ms)
        self.latency_label.configure(text_color=col)
        self.status_indicator.configure(text_color=col)

    def _zen_resize_start(self, event):
        if not self._zen:
            return
        # store size, mouse pos, and the ORIGINAL top-left anchor
        self._resize_start_geo = (
            self.winfo_width(),
            self.winfo_height(),
            event.x_root,
            event.y_root,
            self.winfo_x(),
            self.winfo_y(),
        )

    def _zen_resize_drag(self, event):
        if not self._zen or not getattr(self, "_resize_start_geo", None):
            return

        w0, h0, sx, sy, ax, ay = self._resize_start_geo

        # grow/shrink as you drag bottom-right, keep square
        delta = max(event.x_root - sx, event.y_root - sy)
        side = max(180, min(1000, int(min(w0, h0) + delta)))

        # keep the ORIGINAL top-left fixed ⇒ no drift
        self.geometry(f"{side}x{side}+{ax}+{ay}")
        self.cfg["window_size"] = side

        # debounce saving config
        if hasattr(self, "_resize_save_after_id"):
            self.after_cancel(self._resize_save_after_id)
        self._resize_save_after_id = self.after(500, lambda: save_config(self.cfg))

    # ------------- wake / timers -------------
    def _wake_from_double_click(self, event=None):
        if self._zen:
            self._exit_zen()
        self._reset_idle_timer()

    def _reset_idle_timer(self, event=None):
        if self._idle_after_id is not None:
            try: self.after_cancel(self._idle_after_id)
            except Exception: pass
            self._idle_after_id = None
        
        secs = float(self.cfg.get("auto_hide_sec", 0.0) or 0.0)
        if secs > 0:
            self._idle_after_id = self.after(int(secs * 1000), self._enter_zen)

    # ------------- Zen enter/exit -------------
    def _enter_zen(self):
        if self._zen: 
            return
    
        self._zen = True
        self._apply_minimum_size()
        self._pre_zen_geometry = self.geometry()
    
        self._zen_bg_color = self._color_for_value(self._last_ms_or_none())
        self.configure(fg_color=self._zen_bg_color)
        text_color = self._color_for_bg(self._zen_bg_color)

        try:
            self.overrideredirect(True)
        except Exception:
            pass

        self.top_frame.forget()
        self.canvas_frame.forget()
        self.center_row.forget()

        side = int(self.cfg.get("window_size", 320))
        self.geometry(f"{side}x{side}")

        self.zen_label.configure(text_color=text_color, fg_color="transparent")
        self.zen_label.place(relx=0.5, rely=0.5, anchor="center")
    
        self.zen_avg_caption.configure(text_color=text_color)
        self.zen_avg_label.configure(text_color=text_color)
        self.zen_avg_caption.place(x=8, y=8, anchor="nw")
        self.zen_avg_label.place(x=8, y=32, anchor="nw")

        self.zen_grip.lift()
        self.zen_grip.place(relx=1.0, rely=1.0, anchor="se")

        self.bind_all("<ButtonPress-1>", self._zen_drag_start, add="+")
        self.bind_all("<B1-Motion>", self._zen_drag_move, add="+")

        self._fit_zen_fonts()

    def _exit_zen(self):
        if not self._zen: 
            return
    
        self._zen = False
    
        try: 
            self.overrideredirect(False)
        except Exception: 
            pass

        self.unbind_all("<ButtonPress-1>")
        self.unbind_all("<B1-Motion>")

        self.zen_label.place_forget()
        self.zen_avg_caption.place_forget()
        self.zen_avg_label.place_forget()
        self.zen_grip.place_forget()

        self.top_frame.pack(side="top", fill="x", padx=4, pady=4)
        self.center_row.pack(side="top", fill="x", padx=10, pady=(2, 0))
        self.canvas_frame.pack(side="top", fill="both", expand=True, padx=10, pady=(4, 10))

        self.configure(fg_color=self._normal_fg_color)

        if self._pre_zen_geometry:
            self.geometry(self._pre_zen_geometry)

        self._apply_minimum_size()
        self.update_latency_color(self._last_ms_or_none())
        self.draw_bars()

    # ------------- Zen drag / resize -------------
    def _zen_drag_start(self, event):
        if not self._zen:
            return
        # don't start drag when grabbing the resize grip
        if event.widget is self.zen_grip:
            return
        self._drag_off = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _zen_drag_move(self, event):
        if not self._zen:
            return
        # ignore drag if we're on the resize grip
        if event.widget is self.zen_grip:
            return
        dx, dy = self._drag_off
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")


    def _zen_wheel_resize(self, event):
        if not self._zen:
            return
    
        ax, ay = self.winfo_x(), self.winfo_y()
        step = 15 if event.delta > 0 else -15
        side = max(180, min(1000, int(self.winfo_width() + step)))
        
        self.geometry(f"{side}x{side}+{ax}+{ay}")
        self.cfg["window_size"] = side
        
        if hasattr(self, '_resize_save_after_id'):
            self.after_cancel(self._resize_save_after_id)
        self._resize_save_after_id = self.after(500, lambda: save_config(self.cfg))

    def _fit_zen_fonts(self):
        if not self._zen:
            return
        
        self.update_idletasks()
        if self.winfo_width() < 50 or self.winfo_height() < 50:
            return

        w = max(50, self.winfo_width())
        h = max(50, self.winfo_height())

        box_w = int(w * 0.92)
        box_h = int(h * 0.88)
        txt = self.latency_var.get() or "- ms"

        probe = tkfont.Font(family="Segoe UI", weight="bold")
        lo, hi = 12, max(28, int(min(box_w, box_h) * 0.8))
        best = lo
        while lo <= hi:
            mid = (lo + hi) // 2
            probe.configure(size=mid)
            tw = probe.measure(txt)
            th = probe.metrics("ascent") + probe.metrics("descent")
            if tw <= box_w and th <= box_h:
                best = mid; lo = mid + 1
            else:
                hi = mid - 1
        self.zen_font.configure(size=best)
        self.zen_label.configure(font=self.zen_font)

        side = min(w, h)
        cap_size = max(10, int(side * 0.035))
        val_size = max(18, int(side * 0.12))
        self.zen_avg_font_caption.configure(size=cap_size)
        self.zen_avg_font_value.configure(size=val_size)
        self.zen_avg_caption.configure(font=self.zen_avg_font_caption)
        self.zen_avg_label.configure(font=self.zen_avg_font_value)

    # ------------- ping loop -------------
    def ping_loop(self):
        while not self._stop_evt.is_set():
            ms = ping_once_ms(self._current_host)
            self._q.put((time.time(), ms))
            wait = max(0.2, min(60, float(self.cfg.get("refresh_sec", 2.0))))
            self._stop_evt.wait(wait)

    def _last_ms_or_none(self):
        if not self.history: 
            return None
        v = self.history[-1]
        return None if v is None or v == float("inf") else v

    def drain_queue(self):
        try:
            changed = False
            while True:
                ts, ms = self._q.get_nowait()

                # Normalize ms: None for lost packets
                ms_val = None if ms == float("inf") else float(ms)

                # Main latency label
                shown = f"{int(ms)} ms" if ms_val is not None else "- ms"
                self.latency_var.set(shown)

                # History for chart / Zen
                self.history.append(ms_val)
                keep = HISTORY_LENGTH_ZEN if self._zen else HISTORY_LENGTH_FULL
                self.history = self.history[-keep:]

                # Recent minute buffer for avg / loss
                self.recent_minute.append((ts, ms_val))
                cutoff = ts - 60.0
                while self.recent_minute and self.recent_minute[0][0] < cutoff:
                    self.recent_minute.popleft()

                # Average over last 60s
                vals = [v for (_, v) in self.recent_minute if v is not None]
                avg = (sum(vals) / len(vals)) if vals else None
                self.avg_var.set(f"{int(avg)}" if avg is not None else "-")

                # Packet loss over last 60s
                loss_count = len([1 for (_, v) in self.recent_minute if v is None])
                total = len(self.recent_minute)
                loss_pct = (loss_count / total * 100.0) if total > 0 else 0.0
                self.loss_var.set(f"{loss_pct:.0f}% loss")

                # Colors
                self.update_latency_color(self._last_ms_or_none())
                self.update_avg_color(avg)

                changed = True

        except queue.Empty:
            pass

        if changed:
            if not self._zen:
                self.draw_bars()
            else:
                bg = self._color_for_value(self._last_ms_or_none())
                self.configure(fg_color=bg)
                text_color = self._color_for_bg(bg)

                self.zen_label.configure(text_color=text_color)
                self.zen_avg_caption.configure(text_color=text_color)
                self.zen_avg_label.configure(text_color=text_color)

                self._fit_zen_fonts()

        self.after(150, self.drain_queue)


    # ------------- bars with mini numbers -------------
    def draw_bars(self):
        self.ax.clear()
        self.ax.axis("off")
        if not self.history:
            self.spark_canvas.draw()
            return

        N = HISTORY_LENGTH_FULL
        data = self.history[-N:]
        vals = [0 if v is None else v for v in data]
        xs = list(range(len(vals)))
        colors = [self._color_for_value(v) for v in data]

        max_v = max(vals) if vals else 1
        y_max = max(50, min(800, int(max_v * 1.3)))
        self.ax.set_ylim(0, y_max)
        bars = self.ax.bar(xs, vals, color=colors, width=0.8)
        self.ax.margins(x=0)

        for x, v, bar in zip(xs, data, bars):
            label = "-" if v is None else f"{int(v)}"
            height = bar.get_height()
            self.ax.text(x, height + (y_max * 0.03), label, ha="center", va="bottom", fontsize=8)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            self.fig.tight_layout(pad=0.15)
    
        self.spark_canvas.draw()

    # ------------- colors -------------
    def update_avg_color(self, avg):
        col = self._color_for_value(avg)
        self.avg_label.configure(text_color=col)
        self.avg_caption.configure(text_color=col)

    # ------------- UI -------------
    def save_host(self):
        self._current_host = (self.host_var.get() or "").strip() or "google.com"
        self.cfg["host"] = self._current_host
        save_config(self.cfg)

    def open_settings(self):
        win = ctk.CTkToplevel(self)
        win.title("Settings")
        win.transient(self)
        win.attributes("-topmost", True)
        win.lift()
        win.resizable(False, False)

        try:
            self.update_idletasks()
            sw, sh = 360, 180
            mx, my = self.winfo_x(), self.winfo_y()
            mw, mh = self.winfo_width(), self.winfo_height()
            x = mx + (mw - sw) // 2
            y = my + (mh - sh) // 2
            win.geometry(f"+{max(0,x)}+{max(0,y)}")
        except Exception:
            pass

        frm = ctk.CTkFrame(win)
        frm.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(frm, text="Refresh interval (sec):").grid(row=0, column=0, sticky="w", padx=(0,8), pady=(0,8))
        refresh_var = ctk.StringVar(value=str(self.cfg.get("refresh_sec", 2.0)))
        ctk.CTkEntry(frm, textvariable=refresh_var, width=90).grid(row=0, column=1, sticky="w", pady=(0,8))

        ctk.CTkLabel(frm, text="Auto-hide after (sec, 0 = never):").grid(row=1, column=0, sticky="w", padx=(0,8), pady=(0,8))
        auto_var = ctk.StringVar(value=str(self.cfg.get("auto_hide_sec", 0.0)))
        ctk.CTkEntry(frm, textvariable=auto_var, width=90).grid(row=1, column=1, sticky="w", pady=(0,8))

        def save_settings():
            try:
                v = float(refresh_var.get())
                v = max(0.2, min(60.0, v))
                self.cfg["refresh_sec"] = v
            except ValueError: 
                pass
            
            try:
                a = float(auto_var.get())
                a = max(0.0, min(3600.0, a))
                self.cfg["auto_hide_sec"] = a
            except ValueError: 
                pass
            
            save_config(self.cfg)
            self._reset_idle_timer()
            win.destroy()

        btns = ctk.CTkFrame(frm)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8,0))
        ctk.CTkButton(btns, text="Cancel", width=72, command=win.destroy).pack(side="right", padx=(0,6))
        ctk.CTkButton(btns, text="Save", width=72, command=save_settings).pack(side="right")

        frm.columnconfigure(0, weight=0)
        frm.columnconfigure(1, weight=1)

    # ------------- Tray / lifecycle -------------
    def on_close(self):
        self._stop_evt.set()
        try: 
            self._tray_icon.stop()
        except Exception: 
            pass
        self.destroy()

    def show_window(self, icon, item):
        self.after(0, self.deiconify)
        try: 
            icon.stop()
        except Exception: 
            pass

    def tray_quit(self, icon, item):
        try: 
            icon.stop()
        except Exception: 
            pass
        self.on_close()

def main():
    cfg = load_config()
    app = ModernPingWidget(cfg)
    app.mainloop()

if __name__ == "__main__":
    main()
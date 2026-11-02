# -*- coding: utf-8 -*-
"""
ระบบแจ้งเตือนตารางสอนคาบละ 40 นาที
ครูทวีวัฒน์ คีรีสูงเนิน (ครูเม่น) - โรงเรียนสารวิทยา

การทำงาน:
- แจ้งเตือนล่วงหน้า 1 คาบ (ต้นคาบ 1 เตือนคาบ 2, ต้นคาบ 2 เตือนคาบ 3, ...)
- การ์ดลอยมุมขวาล่าง ไม่หายไปจนกว่าจะกด "รับทราบ"
- ถึงเวลาเตือนคาบใหม่ ปิดการ์ดเก่าอัตโนมัติแล้วแสดงการ์ดใหม่
- คาบว่าง: บอกคาบถัดไปที่มีสอน
- ไม่มีเสียง

โหมดการรัน:
  pythonw teacher_notifier.pyw              ทำงานพื้นหลัง (เปิดได้ครั้งละ 1 ตัว)
  pythonw teacher_notifier.pyw --test       แสดงการ์ดตัวอย่าง (มีสอน) แล้วปิดตัวเอง
  pythonw teacher_notifier.pyw --test-free  แสดงการ์ดตัวอย่าง (คาบว่าง) แล้วปิดตัวเอง
  python  teacher_notifier.pyw --status     พิมพ์เวลาแจ้งเตือนทั้งหมด
"""

import sys
import os
import json
import socket
import logging
import traceback
from datetime import datetime, timedelta
import tkinter as tk

# ---------------------------------------------------------------------------
# ค่าคงที่
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(SCRIPT_DIR, "schedule_data.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "notifier.log")

LOCK_PORT = 47821            # พอร์ต localhost ใช้กันเปิดซ้ำ + ให้หน้าต่างควบคุมตรวจสถานะ
CHECK_INTERVAL_MS = 15_000   # ตรวจเวลาทุก 15 วินาที
GRACE_MINUTES = 10           # เปิดเครื่อง/ตื่นจาก Sleep ช้าไม่เกิน 10 นาที ยังเตือนให้
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

COLOR_TEACH = "#E91E63"
COLOR_FREE = "#3F51B5"
FONT_TH = "Leelawadee UI"

logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO, encoding="utf-8",
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("notifier")

for _stream in (sys.stdout, sys.stderr):
    if _stream:
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ข้อมูลตารางสอน
# ---------------------------------------------------------------------------
class Schedule:
    """อ่านและค้นหาข้อมูลจาก schedule_data.json"""

    def __init__(self, path=DATA_FILE):
        self.path = path
        self._mtime = None
        self.data = {}
        self.reload_if_changed(force=True)

    def reload_if_changed(self, force=False):
        """โหลดใหม่เมื่อไฟล์ถูกแก้ไข (แก้ตารางได้โดยไม่ต้องรีสตาร์ต). ถ้าไฟล์เสีย ใช้ข้อมูลเดิม"""
        try:
            mtime = os.path.getmtime(self.path)
            if not force and mtime == self._mtime:
                return
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["periods"], data["timetable"]  # ตรวจโครงสร้างขั้นต่ำ
            self.data, self._mtime = data, mtime
            log.info("Loaded schedule data")
        except Exception:
            log.error("Cannot load schedule data:\n%s", traceback.format_exc())
            if not self.data:
                self.data = {"periods": [], "timetable": {}}

    @property
    def periods(self):
        return self.data.get("periods", [])

    def period(self, num):
        return next((p for p in self.periods if p["period"] == num), None)

    def notify_map(self):
        """{'HH:MM': เลขคาบที่จะเตือน}"""
        return {p["notify_at"]: p["period"] for p in self.periods if p.get("notify_at")}

    def class_at(self, day, num):
        return self.data.get("timetable", {}).get(day, {}).get("classes", {}).get(str(num))

    def next_class_after(self, day, num):
        last = max((p["period"] for p in self.periods), default=0)
        for n in range(num + 1, last + 1):
            info = self.class_at(day, n)
            if info:
                return self.period(n), info
        return None, None


# ---------------------------------------------------------------------------
# การ์ดแจ้งเตือน
# ---------------------------------------------------------------------------
class NotificationCard:
    WIDTH, HEIGHT = 380, 250

    def __init__(self, master, title, headline, lines, color, on_close=None):
        self.on_close = on_close
        self._drag = (0, 0)

        win = self.window = tk.Toplevel(master)
        win.title(title)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#FCE4EC")
        x = win.winfo_screenwidth() - self.WIDTH - 25
        y = win.winfo_screenheight() - self.HEIGHT - 65
        win.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

        card = tk.Frame(win, bg="#FFFFFF", highlightthickness=2, highlightbackground=color)
        card.pack(fill="both", expand=True, padx=2, pady=2)

        # แถบหัว (ลากย้ายได้)
        header = tk.Frame(card, bg=color, height=42)
        header.pack(fill="x")
        header.pack_propagate(False)
        lbl_title = tk.Label(header, text="🔔  " + title, bg=color, fg="#FFFFFF",
                             font=("Segoe UI", 11, "bold"))
        lbl_title.pack(side="left", padx=12)
        btn_x = tk.Label(header, text="✕", bg=color, fg="#FFFFFF",
                         font=("Segoe UI", 11, "bold"), cursor="hand2")
        btn_x.pack(side="right", padx=10)
        btn_x.bind("<Button-1>", lambda e: self.acknowledge())
        for w in (header, lbl_title):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._do_drag)

        # เนื้อหา
        body = tk.Frame(card, bg="#FFFFFF", padx=16, pady=10)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=headline, bg="#FFFFFF", fg="#212121", font=(FONT_TH, 12, "bold"),
                 wraplength=340, justify="left", anchor="w").pack(fill="x", pady=(0, 6))
        for line in lines:
            tk.Label(body, text=line, bg="#FFFFFF", fg="#424242", font=(FONT_TH, 10),
                     wraplength=340, justify="left", anchor="w").pack(fill="x", pady=1)

        # ปุ่มรับทราบ
        footer = tk.Frame(card, bg="#FAFAFA", height=50)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Button(footer, text="✔  รับทราบ", bg=color, fg="#FFFFFF", activebackground="#C2185B",
                  activeforeground="#FFFFFF", relief="flat", font=(FONT_TH, 11, "bold"),
                  cursor="hand2", padx=20, command=self.acknowledge).pack(side="right", padx=14, pady=8)
        tk.Label(footer, text="สารวิทยา (คาบละ 40 น.)", bg="#FAFAFA", fg="#9E9E9E",
                 font=("Segoe UI", 8)).pack(side="left", padx=14)

    def _start_drag(self, e):
        self._drag = (e.x, e.y)

    def _do_drag(self, e):
        x = self.window.winfo_x() + e.x - self._drag[0]
        y = self.window.winfo_y() + e.y - self._drag[1]
        self.window.geometry(f"+{x}+{y}")

    def acknowledge(self):
        self.close()
        if self.on_close:
            self.on_close(self)

    def close(self):
        try:
            self.window.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# ตัวจัดการแจ้งเตือน
# ---------------------------------------------------------------------------
class TeacherNotifier:
    def __init__(self, root, schedule, run_timer=True):
        self.root = root
        self.schedule = schedule
        self.card = None
        self.fired = set()
        self.fired_date = None
        if run_timer:
            self._check_startup_catchup()
            self._tick()

    def _check_startup_catchup(self):
        """ตรวจสอบเมื่อเปิดเครื่อง/เริ่มโปรแกรม หากเปิดในเวลาเรียน ให้สรุปสถานะคาบปัจจุบันทันที"""
        now = datetime.now()
        day = now.strftime("%A")
        if day not in WEEKDAYS:
            return

        t_start = now.replace(hour=8, minute=20, second=0, microsecond=0)
        t_end = now.replace(hour=15, minute=0, second=0, microsecond=0)

        if t_start <= now <= t_end:
            now_hm = now.strftime("%H:%M")
            for hhmm in self.schedule.notify_map().keys():
                if hhmm <= now_hm:
                    self.fired.add(hhmm)
            self.fired_date = now.date()
            self.show_current_status(day, now)

    # ---- ข้อความ -----------------------------------------------------------
    def build_message(self, day, num):
        meta = self.schedule.period(num)
        info = self.schedule.class_at(day, num)
        if info:
            return (
                f"แจ้งเตือนตารางสอน คาบ {num}",
                f"คุณครูคะ คุณมีสอนคาบ {num} เวลา {meta['start']} น. ค่ะ",
                [f"📖 วิชา: {info['subject']}",
                 f"👥 ชั้นเรียน: {info['class_room']}",
                 f"📍 สถานที่: {info['location']}",
                 f"⏳ เวลาสอน: {meta['start']} - {meta['end']} น. (40 นาที)"],
                COLOR_TEACH,
            )

        nxt_meta, nxt = self.schedule.next_class_after(day, num)
        if nxt:
            headline = (f"คุณครูคะ คาบ {num} ({meta['start']} น.) คุณครูว่าง ไม่มีสอนค่ะ "
                        f"จะมีสอนอีกที คาบ {nxt_meta['period']} เวลา {nxt_meta['start']} น.")
            lines = [f"📖 วิชา: {nxt['subject']} ({nxt['class_room']})",
                     f"📍 สถานที่: {nxt['location']}"]
        else:
            headline = f"คุณครูคะ คาบ {num} ({meta['start']} น.) คุณครูว่าง ไม่มีสอนค่ะ"
            lines = ["🎉 วันนี้ไม่มีคาบสอนแล้วค่ะ", "พักผ่อนได้เลยนะคะคุณครูเม่น 😊"]
        return f"แจ้งเตือนคาบว่าง คาบ {num}", headline, lines, COLOR_FREE

    def build_current_status_message(self, day, now):
        """สร้างข้อความสรุปสถานะคาบปัจจุบัน (สำหรับเปิดคอมช้า หรือกดดูย้อนหลัง)"""
        now_hm = now.strftime("%H:%M")
        curr_p = None
        next_p = None

        for p in self.schedule.periods:
            h1, m1 = map(int, p["start"].split(":"))
            h2, m2 = map(int, p["end"].split(":"))
            t1 = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
            t2 = now.replace(hour=h2, minute=m2, second=0, microsecond=0)
            if t1 <= now < t2:
                curr_p = p
            elif now < t1 and next_p is None:
                next_p = p

        lines = []
        is_teaching_soon = False

        if curr_p:
            num = curr_p["period"]
            info = self.schedule.class_at(day, num)
            if info:
                is_teaching_soon = True
                headline = f"คุณครูคะ ขณะนี้เวลา {now_hm} น. (กำลังอยู่ในคาบ {num})"
                lines.append(f"📍 กำลังสอน: คาบ {num} ({curr_p['start']} - {curr_p['end']} น.)")
                lines.append(f"📖 วิชา: {info['subject']} ({info['class_room']}) | {info['location']}")
            else:
                headline = f"คุณครูคะ ขณะนี้เวลา {now_hm} น. (คาบ {num} เป็นคาบว่าง)"
                lines.append(f"📍 ขณะนี้: คาบ {num} ({curr_p['start']} - {curr_p['end']} น.) [คาบว่าง]")
        else:
            t_first = now.replace(hour=8, minute=20, second=0, microsecond=0)
            if now < t_first:
                headline = f"คุณครูคะ ขณะนี้เวลา {now_hm} น. (ก่อนเริ่มคาบ 1)"
                lines.append("🌅 โรงเรียนเริ่มคาบ 1 เวลา 08:20 น. ค่ะ")
                next_p = self.schedule.period(1)
            else:
                headline = f"คุณครูคะ ขณะนี้เวลา {now_hm} น. (เลิกเรียนแล้ว)"
                lines.append("🎉 สิ้นสุดการเรียนการสอนของวันนี้แล้วค่ะ (หลัง 15:00 น.)")

        if next_p:
            n_num = next_p["period"]
            n_info = self.schedule.class_at(day, n_num)
            if n_info:
                is_teaching_soon = True
                lines.append(f"⏳ คาบถัดไป: คาบ {n_num} ({next_p['start']} น.) {n_info['subject']} ({n_info['class_room']})")
            else:
                lines.append(f"☕ คาบถัดไป: คาบ {n_num} ({next_p['start']} น.) [คาบว่าง]")
                nxt_meta, nxt_cls = self.schedule.next_class_after(day, n_num)
                if nxt_cls:
                    lines.append(f"✨ มีสอนอีกที: คาบ {nxt_meta['period']} ({nxt_meta['start']} น.) {nxt_cls['subject']}")
                else:
                    lines.append("🎉 หลังจากนี้ไม่มีคาบสอนแล้วสำหรับวันนี้ค่ะ")

        color = COLOR_TEACH if is_teaching_soon else COLOR_FREE
        title = "สรุปสถานะเวลาสอนปัจจุบัน"
        return title, headline, lines, color

    # ---- แสดงการ์ด ---------------------------------------------------------
    def show(self, num, day=None, on_close=None):
        day = day or datetime.now().strftime("%A")
        if not self.schedule.period(num):
            return
        if self.card:                       # ปิดการ์ดเก่าอัตโนมัติ
            self.card.close()
        title, headline, lines, color = self.build_message(day, num)

        def _closed(card):
            if self.card is card:
                self.card = None
            if on_close:
                on_close()

        self.card = NotificationCard(self.root, title, headline, lines, color, _closed)
        log.info("Shown notification for period %s (%s)", num, day)

    def show_current_status(self, day=None, now=None, on_close=None):
        now = now or datetime.now()
        day = day or now.strftime("%A")
        if self.card:
            self.card.close()
        title, headline, lines, color = self.build_current_status_message(day, now)

        def _closed(card):
            if self.card is card:
                self.card = None
            if on_close:
                on_close()

        self.card = NotificationCard(self.root, title, headline, lines, color, _closed)
        log.info("Shown current status notification for %s (%s)", now.strftime("%H:%M"), day)

    # ---- ตัวจับเวลา --------------------------------------------------------
    def _tick(self):
        try:
            self.schedule.reload_if_changed()
            now = datetime.now()
            today = now.date()
            if self.fired_date != today:
                self.fired, self.fired_date = set(), today

            if now.strftime("%A") in WEEKDAYS:
                due = []
                for hhmm, num in self.schedule.notify_map().items():
                    h, m = map(int, hhmm.split(":"))
                    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    if hhmm not in self.fired and t <= now <= t + timedelta(minutes=GRACE_MINUTES):
                        due.append((t, hhmm, num))
                for t, hhmm, num in sorted(due):   # ถ้าค้างหลายอัน แสดงอันล่าสุด
                    self.fired.add(hhmm)
                if due:
                    self.show(sorted(due)[-1][2], now.strftime("%A"))
        except Exception:
            log.error("Tick error:\n%s", traceback.format_exc())
        finally:
            self.root.after(CHECK_INTERVAL_MS, self._tick)


# ---------------------------------------------------------------------------
# กันเปิดซ้ำ
# ---------------------------------------------------------------------------
def acquire_single_instance():
    """คืน socket ที่จองพอร์ตไว้ หรือ None ถ้ามีตัวอื่นทำงานอยู่แล้ว"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def run_test(period, day):
    root = tk.Tk()
    root.withdraw()
    app = TeacherNotifier(root, Schedule(), run_timer=False)
    app.show(period, day, on_close=root.destroy)
    root.mainloop()


def print_status():
    sch = Schedule()
    print(f"Now: {datetime.now():%Y-%m-%d %H:%M (%A)}")
    for p in sch.periods:
        if p.get("notify_at"):
            print(f"  {p['notify_at']} -> คาบ {p['period']} ({p['start']}-{p['end']})")


def run_current_status(day=None):
    root = tk.Tk()
    root.withdraw()
    app = TeacherNotifier(root, Schedule(), run_timer=False)
    app.show_current_status(day, on_close=root.destroy)
    root.mainloop()


def main():
    args = sys.argv[1:]
    if "--status" in args:
        return print_status()
    if "--test" in args:
        return run_test(2, "Monday")
    if "--test-free" in args:
        return run_test(3, "Monday")
    if "--show-current" in args:
        return run_current_status()

    lock = acquire_single_instance()
    if lock is None:
        log.info("Another instance is already running. Exit.")
        return
    log.info("Notifier started (pid %s)", os.getpid())
    root = tk.Tk()
    root.withdraw()
    TeacherNotifier(root, Schedule())
    try:
        root.mainloop()
    finally:
        lock.close()
        log.info("Notifier stopped")


if __name__ == "__main__":
    main()

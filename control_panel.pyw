# -*- coding: utf-8 -*-
"""
หน้าต่างควบคุมระบบแจ้งเตือนตารางสอน (ครูเม่น - ร.ร.สารวิทยา)
- ดูตารางสอนรายวัน & ดับเบิ้ลคลิกแก้ไขรายคาบได้
- สแกนตารางสอนอัตโนมัติจากไฟล์รูปภาพ หรือวางรูปที่แคปหน้าจอ (Ctrl+V) ด้วย AI
- ทดสอบการแจ้งเตือน
- เริ่ม/หยุดระบบ, ตั้งเปิดพร้อม Windows
งานที่ใช้เวลา (สแกนรูป AI, สร้าง shortcut, หยุดโปรเซส) รันใน thread แยก หน้าต่างจึงไม่ค้าง
"""

import os
import sys
import socket
import threading
import subprocess
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, SCRIPT_DIR)
from teacher_notifier import Schedule, LOCK_PORT, WEEKDAYS  # noqa: E402
import image_scanner  # noqa: E402

NOTIFIER_PYW = os.path.join(SCRIPT_DIR, "teacher_notifier.pyw")
NOTIFIER_EXE = os.path.join(SCRIPT_DIR, "teacher_notifier.exe")
STARTUP_LNK = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs\Startup\TeacherScheduleNotifier.lnk",
)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
)
DAYS_TH = [
    ("วันจันทร์", "Monday"),
    ("วันอังคาร", "Tuesday"),
    ("วันพุธ", "Wednesday"),
    ("วันพฤหัสบดี", "Thursday"),
    ("วันศุกร์", "Friday"),
]


# ---------------------------------------------------------------------------
# ฟังก์ชันระบบ
# ---------------------------------------------------------------------------
def pythonw_path():
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return cand if os.path.exists(cand) else sys.executable


def is_running():
    """ตรวจทันทีจากพอร์ตล็อกของตัวแจ้งเตือน (ไม่ต้องเรียก PowerShell)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", LOCK_PORT)) == 0


def start_notifier():
    if os.path.exists(NOTIFIER_EXE):
        subprocess.Popen(
            [NOTIFIER_EXE],
            cwd=SCRIPT_DIR,
            creationflags=DETACHED,
            close_fds=True,
        )
    else:
        subprocess.Popen(
            [pythonw_path(), NOTIFIER_PYW],
            cwd=SCRIPT_DIR,
            creationflags=DETACHED,
            close_fds=True,
        )


def run_test(flag):
    if os.path.exists(NOTIFIER_EXE):
        subprocess.Popen(
            [NOTIFIER_EXE, flag], cwd=SCRIPT_DIR, close_fds=True
        )
    else:
        subprocess.Popen(
            [pythonw_path(), NOTIFIER_PYW, flag], cwd=SCRIPT_DIR, close_fds=True
        )


def stop_notifier():
    if os.path.exists(NOTIFIER_EXE):
        ps = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -like 'teacher_notifier*' -and $_.CommandLine -notlike '*--test*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
    else:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
            "Where-Object { $_.CommandLine -like '*teacher_notifier.pyw*' -and $_.CommandLine -notlike '*--test*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        creationflags=NO_WINDOW,
        timeout=30,
    )


def autostart_enabled():
    return os.path.exists(STARTUP_LNK)


def create_autostart():
    if os.path.exists(NOTIFIER_EXE):
        target_exe = NOTIFIER_EXE
        args_str = '""'
    else:
        target_exe = pythonw_path()
        args_str = '[char]34+$env:TSN_SCRIPT+[char]34'

    env = dict(
        os.environ,
        TSN_LNK=STARTUP_LNK,
        TSN_EXE=target_exe,
        TSN_SCRIPT=NOTIFIER_PYW,
        TSN_DIR=SCRIPT_DIR,
    )
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:TSN_LNK);"
        "$s.TargetPath=$env:TSN_EXE;"
        f"$s.Arguments={args_str};"
        "$s.WorkingDirectory=$env:TSN_DIR;$s.Save()"
    )
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps,
        ],
        env=env,
        creationflags=NO_WINDOW,
        timeout=30,
        check=True,
    )


def remove_autostart():
    if os.path.exists(STARTUP_LNK):
        os.remove(STARTUP_LNK)


def count_total_classes(schedule_dict):
    tt = schedule_dict.get("timetable", {})
    return sum(len(day_obj.get("classes", {})) for day_obj in tt.values())


# ---------------------------------------------------------------------------
# หน้าต่างแก้ไขรายคาบ (เมื่อดับเบิ้ลคลิกแถวในตาราง)
# ---------------------------------------------------------------------------
class EditPeriodDialog:
    def __init__(self, parent, day_th, period_num, current_info, on_save):
        self.on_save = on_save
        self.win = tk.Toplevel(parent)
        self.win.title(f"แก้ไขตารางสอน - {day_th} คาบที่ {period_num}")
        self.win.geometry("380x250")
        self.win.configure(bg="#FFFFFF")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()

        tk.Label(
            self.win,
            text=f"✏️ แก้ไขข้อมูล {day_th} (คาบที่ {period_num})",
            bg="#FFFFFF",
            fg="#E91E63",
            font=("Leelawadee UI", 11, "bold"),
        ).pack(anchor="w", padx=18, pady=(14, 8))

        form = tk.Frame(self.win, bg="#FFFFFF", padx=18)
        form.pack(fill="x")

        tk.Label(form, text="รหัส/ชื่อวิชา:", bg="#FFFFFF", font=("Leelawadee UI", 10)).grid(
            row=0, column=0, sticky="w", pady=4
        )
        self.ent_subj = tk.Entry(form, font=("Leelawadee UI", 10), width=28)
        self.ent_subj.grid(row=0, column=1, pady=4, padx=6)
        self.ent_subj.insert(0, current_info.get("subject", ""))

        tk.Label(form, text="ชั้น/ห้องเรียน:", bg="#FFFFFF", font=("Leelawadee UI", 10)).grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.ent_room = tk.Entry(form, font=("Leelawadee UI", 10), width=28)
        self.ent_room.grid(row=1, column=1, pady=4, padx=6)
        self.ent_room.insert(0, current_info.get("class_room", ""))

        tk.Label(form, text="สถานที่เรียน:", bg="#FFFFFF", font=("Leelawadee UI", 10)).grid(
            row=2, column=0, sticky="w", pady=4
        )
        self.ent_loc = tk.Entry(form, font=("Leelawadee UI", 10), width=28)
        self.ent_loc.grid(row=2, column=1, pady=4, padx=6)
        self.ent_loc.insert(0, current_info.get("location", ""))

        btn_bar = tk.Frame(self.win, bg="#FFFFFF")
        btn_bar.pack(fill="x", padx=18, pady=16)

        tk.Button(
            btn_bar,
            text="🗑️ ตั้งเป็นคาบว่าง",
            bg="#78909C",
            fg="#FFFFFF",
            font=("Leelawadee UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.clear_period,
        ).pack(side="left")

        tk.Button(
            btn_bar,
            text="💾 บันทึก",
            bg="#E91E63",
            fg="#FFFFFF",
            font=("Leelawadee UI", 10, "bold"),
            relief="flat",
            padx=16,
            pady=4,
            cursor="hand2",
            command=self.save,
        ).pack(side="right")

    def clear_period(self):
        self.on_save(None)
        self.win.destroy()

    def save(self):
        subj = self.ent_subj.get().strip()
        room = self.ent_room.get().strip()
        loc = self.ent_loc.get().strip()
        if not subj:
            self.on_save(None)
        else:
            self.on_save({"subject": subj, "class_room": room or "-", "location": loc or "-"})
        self.win.destroy()


# ---------------------------------------------------------------------------
# หน้าต่างหลัก
# ---------------------------------------------------------------------------
class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.schedule = Schedule()
        self.busy = False
        root.title("ระบบแจ้งเตือนตารางสอน คาบละ 40 นาที - โรงเรียนสารวิทยา")
        root.geometry("800x640")
        root.configure(bg="#FCE4EC")
        root.resizable(False, False)

        # รองรับกด Ctrl+V เพื่อวางรูปที่แคปหน้าจอได้ทันที
        root.bind("<Control-v>", lambda e: self.scan_from_clipboard())
        root.bind("<Control-V>", lambda e: self.scan_from_clipboard())

        self._build()
        self._refresh_loop()

    # ---- UI ----------------------------------------------------------------
    def _btn(self, parent, text, color, cmd, side="left", padx=4):
        b = tk.Button(
            parent,
            text=text,
            bg=color,
            fg="#FFFFFF",
            font=("Leelawadee UI", 9, "bold"),
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=5,
            command=cmd,
        )
        b.pack(side=side, padx=padx)
        return b

    def _build(self):
        d = self.schedule.data
        header = tk.Frame(self.root, bg="#E91E63", height=75)
        header.pack(fill="x")
        header.pack_propagate(False)
        curr_dur = d.get("period_duration_minutes", 40)
        self.lbl_main_title = tk.Label(
            header,
            text=f"🌸 ระบบแจ้งเตือนตารางสอน (คาบละ {curr_dur} นาที)",
            bg="#E91E63",
            fg="#FFFFFF",
            font=("Leelawadee UI", 14, "bold"),
        )
        self.lbl_main_title.pack(anchor="w", padx=20, pady=(10, 2))
        self.lbl_teacher_info = tk.Label(
            header,
            text=self._header_subtitle(d),
            bg="#E91E63",
            fg="#F8BBD0",
            font=("Leelawadee UI", 10),
        )
        self.lbl_teacher_info.pack(anchor="w", padx=20)

        # Status Bar
        status = tk.Frame(self.root, bg="#FFFFFF", bd=1, relief="solid")
        status.pack(fill="x", padx=16, pady=(10, 6))
        self.lbl_status = tk.Label(
            status, bg="#FFFFFF", font=("Leelawadee UI", 10, "bold")
        )
        self.lbl_status.pack(side="left", padx=15, pady=7)
        self.lbl_auto = tk.Label(status, bg="#FFFFFF", font=("Leelawadee UI", 9))
        self.lbl_auto.pack(side="right", padx=15, pady=7)

        # Row 1: ปุ่มควบคุมระบบและการแจ้งเตือน
        bar1 = tk.Frame(self.root, bg="#FCE4EC")
        bar1.pack(fill="x", padx=12, pady=3)
        self._btn(bar1, "🔔 ทดสอบ (มีสอน)", "#E91E63", lambda: run_test("--test"))
        self._btn(bar1, "☕ ทดสอบ (คาบว่าง)", "#3F51B5", lambda: run_test("--test-free"))
        self._btn(bar1, "🔄 คาบปัจจุบัน", "#00897B", lambda: run_test("--show-current"))
        self.btn_run = self._btn(bar1, "", "#4CAF50", self.toggle_service)
        self.btn_auto = self._btn(
            bar1, "", "#607D8B", self.toggle_autostart, side="right"
        )

        # Row 2: กล่องเครื่องมือ AI สแกนรูปตารางสอน
        ai_card = tk.Frame(self.root, bg="#FFF8E1", bd=1, relief="solid")
        ai_card.pack(fill="x", padx=16, pady=6)

        tk.Label(
            ai_card,
            text="✨ อัปเดตตารางสอนด้วย AI:",
            bg="#FFF8E1",
            fg="#E65100",
            font=("Leelawadee UI", 10, "bold"),
        ).pack(side="left", padx=(12, 6), pady=8)

        self.btn_scan_file = self._btn(
            ai_card,
            "📷 เลือกรูปตารางสอน",
            "#FF6F00",
            self.scan_from_file,
            padx=4,
        )
        self.btn_scan_clip = self._btn(
            ai_card,
            "📋 วางรูปที่แคปหน้าจอ (Ctrl+V)",
            "#00897B",
            self.scan_from_clipboard,
            padx=4,
        )
        self._btn(
            ai_card,
            "🔑 ตั้งค่า API Key",
            "#8D6E63",
            self.open_api_key_dialog,
            side="right",
            padx=10,
        )

        # ตารางสอนรายวัน
        view = tk.Frame(self.root, bg="#FFFFFF", bd=1, relief="solid")
        view.pack(fill="both", expand=True, padx=16, pady=(4, 14))

        top_view = tk.Frame(view, bg="#FFFFFF")
        top_view.pack(fill="x", padx=14, pady=(8, 4))
        tk.Label(
            top_view,
            text="📅 ตารางสอนและเวลาแจ้งเตือน",
            bg="#FFFFFF",
            font=("Leelawadee UI", 11, "bold"),
        ).pack(side="left")
        tk.Label(
            top_view,
            text="💡 ดับเบิ้ลคลิกที่แถวใดก็ได้เพื่อแก้ไขรายคาบ",
            bg="#FFFFFF",
            fg="#757575",
            font=("Leelawadee UI", 9),
        ).pack(side="right")

        # Row: เลือกระยะเวลาต่อคาบ (45 / 40 / 50 นาที หรือ กำหนดเอง)
        duration_frame = tk.Frame(view, bg="#F3E5F5", bd=1, relief="solid")
        duration_frame.pack(fill="x", padx=14, pady=(2, 6))

        tk.Label(
            duration_frame,
            text="⏱️ ความยาวคาบเรียน:",
            bg="#F3E5F5",
            fg="#4A148C",
            font=("Leelawadee UI", 9, "bold"),
        ).pack(side="left", padx=(10, 6), pady=4)

        curr_dur = self.schedule.data.get("period_duration_minutes", 40)
        self.dur_var = tk.IntVar(value=curr_dur)

        for d_mins, label_text in [
            (45, "45 นาที (ร่น 5 น.)"),
            (40, "40 นาที (ร่น 10 น.)"),
            (50, "50 นาที (ปกติ)"),
        ]:
            rb = tk.Radiobutton(
                duration_frame,
                text=label_text,
                value=d_mins,
                variable=self.dur_var,
                bg="#F3E5F5",
                activebackground="#F3E5F5",
                selectcolor="#CE93D8",
                font=("Leelawadee UI", 9),
                command=self.on_duration_preset_changed,
            )
            rb.pack(side="left", padx=4)

        btn_custom = tk.Button(
            duration_frame,
            text="⚙️ กำหนดเวลาเอง...",
            bg="#8E24AA",
            fg="#FFFFFF",
            font=("Leelawadee UI", 8, "bold"),
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=2,
            command=self.open_custom_duration_dialog,
        )
        btn_custom.pack(side="right", padx=10, pady=3)

        days = tk.Frame(view, bg="#FFFFFF")
        days.pack(fill="x", padx=14)
        today = datetime.now().strftime("%A")
        self.day = tk.StringVar(value=today if today in WEEKDAYS else "Monday")
        for th, en in DAYS_TH:
            tk.Radiobutton(
                days,
                text=th,
                value=en,
                variable=self.day,
                indicatoron=False,
                bg="#F5F5F5",
                selectcolor="#F8BBD0",
                font=("Leelawadee UI", 9, "bold"),
                padx=12,
                pady=4,
                relief="flat",
                command=self.render_table,
            ).pack(side="left", padx=3)

        cols = [
            ("period", "คาบ", 50, "center"),
            ("time", "เวลาสอน", 115, "center"),
            ("notify", "เวลาเด้งเตือน", 125, "center"),
            ("subject", "วิชา", 140, "w"),
            ("room", "ห้องเรียน", 100, "center"),
            ("loc", "สถานที่", 190, "w"),
        ]
        self.tree = ttk.Treeview(
            view, columns=[c[0] for c in cols], show="headings", height=10
        )
        for key, text, width, anchor in cols:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.pack(fill="both", expand=True, padx=14, pady=8)
        self.tree.bind("<Double-1>", self.on_row_double_click)
        self.render_table()

    def _header_subtitle(self, d):
        total = count_total_classes(d)
        return f"{d.get('teacher_name', '')}  |  {d.get('school_name', '')} ({d.get('semester', '')})  |  รวม {total} คาบ"

    def render_table(self):
        self.tree.delete(*self.tree.get_children())
        day = self.day.get()
        for p in self.schedule.periods:
            c = self.schedule.class_at(day, p["period"]) or {}
            self.tree.insert(
                "",
                "end",
                iid=str(p["period"]),
                values=(
                    p["period"],
                    f"{p['start']} - {p['end']}",
                    f"{p['notify_at']} น." if p.get("notify_at") else "(ไม่เตือน)",
                    c.get("subject", "— ว่าง —"),
                    c.get("class_room", "—"),
                    c.get("location", "—"),
                ),
            )

    # ---- ปรับเปลี่ยนความยาวคาบเรียน (45 / 40 / 50 นาที หรือ กำหนดเอง) --------
    def on_duration_preset_changed(self):
        new_dur = self.dur_var.get()
        first_start = self.schedule.data.get("first_period_start", "08:20")
        count = len(self.schedule.periods) or 10

        new_periods = image_scanner.generate_periods(new_dur, first_start, count)
        self.schedule.data["period_duration_minutes"] = new_dur
        self.schedule.data["first_period_start"] = first_start
        self.schedule.data["periods"] = new_periods

        image_scanner.save_schedule_data(self.schedule.data)
        self.schedule.reload_if_changed(force=True)

        self.lbl_main_title.config(text=f"🌸 ระบบแจ้งเตือนตารางสอน (คาบละ {new_dur} นาที)")
        self.lbl_teacher_info.config(text=self._header_subtitle(self.schedule.data))
        self.render_table()
        messagebox.showinfo("ปรับเวลาคาบเรียน", f"ปรับเวลาคาบเรียนเป็น {new_dur} นาที เรียบร้อยแล้วครับ!\nตารางสอนและเวลาแจ้งเตือนถูกคำนวณใหม่เรียบร้อยแล้ว")

    def open_custom_duration_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("กำหนดความยาวคาบและเวลาเริ่ม")
        win.geometry("380x240")
        win.configure(bg="#FFFFFF")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        tk.Label(
            win,
            text="⏱️ กำหนดเวลาคาบเรียนเอง",
            bg="#FFFFFF",
            fg="#4A148C",
            font=("Leelawadee UI", 11, "bold"),
        ).pack(anchor="w", padx=20, pady=(14, 10))

        form = tk.Frame(win, bg="#FFFFFF", padx=20)
        form.pack(fill="x")

        tk.Label(form, text="จำนวนนาทีต่อคาบ:", bg="#FFFFFF", font=("Leelawadee UI", 10)).grid(
            row=0, column=0, sticky="w", pady=6
        )
        ent_dur = tk.Entry(form, font=("Leelawadee UI", 10), width=12)
        ent_dur.grid(row=0, column=1, pady=6, padx=8)
        ent_dur.insert(0, str(self.schedule.data.get("period_duration_minutes", 45)))

        tk.Label(form, text="เวลาเริ่มคาบแรก (HH:MM):", bg="#FFFFFF", font=("Leelawadee UI", 10)).grid(
            row=1, column=0, sticky="w", pady=6
        )
        ent_start = tk.Entry(form, font=("Leelawadee UI", 10), width=12)
        ent_start.grid(row=1, column=1, pady=6, padx=8)
        ent_start.insert(0, self.schedule.data.get("first_period_start", "08:20"))

        def save_custom():
            try:
                d = int(ent_dur.get().strip())
                s = ent_start.get().strip()
                if d <= 0 or d > 120:
                    raise ValueError("จำนวนนาทีต้องอยู่ระหว่าง 1-120")
                if ":" not in s:
                    raise ValueError("รูปแบบเวลาต้องเป็น HH:MM เช่น 08:20")
            except Exception as e:
                messagebox.showerror("ข้อมูลไม่ถูกต้อง", str(e))
                return

            count = len(self.schedule.periods) or 10
            new_periods = image_scanner.generate_periods(d, s, count)
            self.schedule.data["period_duration_minutes"] = d
            self.schedule.data["first_period_start"] = s
            self.schedule.data["periods"] = new_periods

            image_scanner.save_schedule_data(self.schedule.data)
            self.schedule.reload_if_changed(force=True)

            self.dur_var.set(d)
            self.lbl_main_title.config(text=f"🌸 ระบบแจ้งเตือนตารางสอน (คาบละ {d} นาที)")
            self.lbl_teacher_info.config(text=self._header_subtitle(self.schedule.data))
            self.render_table()
            win.destroy()
            messagebox.showinfo("บันทึกแล้ว", f"ปรับเวลาคาบเรียนเป็น {d} นาที (เริ่ม {s} น.) เรียบร้อยแล้วครับ")

        tk.Button(
            win,
            text="💾 บันทึกและคำนวณใหม่",
            bg="#8E24AA",
            fg="#FFFFFF",
            font=("Leelawadee UI", 10, "bold"),
            relief="flat",
            padx=16,
            pady=5,
            cursor="hand2",
            command=save_custom,
        ).pack(pady=16)

    # ---- แก้ไขตารางรายคาบด้วยการดับเบิ้ลคลิก -----------------------------------
    def on_row_double_click(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        period_str = sel[0]
        day_en = self.day.get()
        day_th = dict((en, th) for th, en in DAYS_TH).get(day_en, day_en)
        curr = self.schedule.class_at(day_en, period_str) or {}

        def save_callback(new_val):
            classes = (
                self.schedule.data.setdefault("timetable", {})
                .setdefault(day_en, {"name_th": day_th, "classes": {}})
                .setdefault("classes", {})
            )
            if new_val is None:
                classes.pop(str(period_str), None)
            else:
                classes[str(period_str)] = new_val
            image_scanner.save_schedule_data(self.schedule.data)
            self.schedule.reload_if_changed(force=True)
            self.lbl_teacher_info.config(text=self._header_subtitle(self.schedule.data))
            self.render_table()

        EditPeriodDialog(self.root, day_th, period_str, curr, save_callback)

    # ---- สแกนรูปตารางสอนด้วย AI ---------------------------------------------
    def scan_from_file(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(
            title="เลือกรูปภาพตารางสอน",
            initialdir=SCRIPT_DIR,
            filetypes=[
                ("Image Files", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"),
                ("All Files", "*.*"),
            ],
        )
        if not path:
            return
        self._run_ai_scan(path, f"ไฟล์ {os.path.basename(path)}")

    def scan_from_clipboard(self):
        if self.busy:
            return
        img = image_scanner.grab_clipboard_image()
        if img is None:
            messagebox.showwarning(
                "ไม่พบรูปภาพใน Clipboard",
                "ยังไม่มีรูปภาพที่คัดลอกไว้ใน Clipboard ครับ\n\n"
                "วิธีใช้งาน:\n"
                "1. กดปุ่ม Windows + Shift + S เพื่อแคปหน้าจอรูปตารางสอน\n"
                "2. กลับมากดปุ่ม '📋 วางรูปที่แคปหน้าจอ (Ctrl+V)' อีกครั้ง",
            )
            return
        self._run_ai_scan(img, "รูปที่แคปหน้าจอ (Clipboard)")

    def _run_ai_scan(self, img_or_path, source_label):
        self.busy = True
        self._set_buttons_state("disabled")
        self.lbl_status.config(
            text=f"⏳ AI กำลังอ่านตารางสอนจาก {source_label} รอสักครู่ (ประมาณ 5 วินาที)...",
            fg="#E65100",
        )

        def worker():
            try:
                new_data = image_scanner.scan_schedule_image(img_or_path)
                self.root.after(0, self._on_scan_success, new_data)
            except Exception as e:
                self.root.after(0, self._on_scan_error, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_success(self, new_data):
        self.busy = False
        self._set_buttons_state("normal")
        self.refresh()

        total = count_total_classes(new_data)
        teacher = new_data.get("teacher_name", "คุณครู")
        school = new_data.get("school_name", "")
        sem = new_data.get("semester", "")

        ok = messagebox.askyesno(
            "สแกนตารางสอนสำเร็จ! 🎉",
            f"AI อ่านข้อมูลจากรูปภาพเรียบร้อยแล้วครับ:\n\n"
            f"👤 ชื่อครู: {teacher}\n"
            f"🏫 โรงเรียน: {school}\n"
            f"📅 ภาคเรียน: {sem}\n"
            f"📚 จำนวนคาบสอนที่พบ: {total} คาบ/สัปดาห์\n\n"
            f"ต้องการบันทึกตารางสอนนี้แทนที่ตารางเดิมเลยหรือไม่?",
        )
        if ok:
            image_scanner.save_schedule_data(new_data)
            self.schedule.reload_if_changed(force=True)
            self.lbl_teacher_info.config(text=self._header_subtitle(self.schedule.data))
            self.render_table()
            messagebox.showinfo(
                "บันทึกสำเร็จ",
                "อัปเดตตารางสอนเรียบร้อยแล้วครับ!\nระบบแจ้งเตือนเบื้องหลังจะใช้ตารางใหม่นี้ทันทีโดยอัตโนมัติ",
            )

    def _on_scan_error(self, err_msg):
        self.busy = False
        self._set_buttons_state("normal")
        self.refresh()
        messagebox.showerror("สแกนรูปภาพไม่สำเร็จ", f"เกิดข้อผิดพลาด:\n{err_msg}")

    def open_api_key_dialog(self):
        cfg = image_scanner.load_config()
        win = tk.Toplevel(self.root)
        win.title("ตั้งค่า Gemini API Key")
        win.geometry("460x180")
        win.configure(bg="#FFFFFF")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        tk.Label(
            win,
            text="🔑 Gemini API Key สำหรับระบบ AI สแกนรูปตารางสอน",
            bg="#FFFFFF",
            fg="#212121",
            font=("Leelawadee UI", 10, "bold"),
        ).pack(anchor="w", padx=18, pady=(14, 6))

        ent = tk.Entry(win, font=("Consolas", 10), width=52)
        ent.pack(padx=18, pady=4)
        ent.insert(0, cfg.get("gemini_api_key", ""))

        def save_key():
            cfg["gemini_api_key"] = ent.get().strip()
            image_scanner.save_config(cfg)
            win.destroy()
            messagebox.showinfo("บันทึกแล้ว", "บันทึก Gemini API Key เรียบร้อยแล้วครับ")

        tk.Button(
            win,
            text="💾 บันทึก API Key",
            bg="#E91E63",
            fg="#FFFFFF",
            font=("Leelawadee UI", 10, "bold"),
            relief="flat",
            padx=16,
            pady=5,
            cursor="hand2",
            command=save_key,
        ).pack(pady=14)

    # ---- งานเบื้องหลัง ------------------------------------------------------
    def _set_buttons_state(self, state):
        for b in (self.btn_run, self.btn_auto, self.btn_scan_file, self.btn_scan_clip):
            b.config(state=state)

    def _background(self, work, done_msg):
        if self.busy:
            return
        self.busy = True
        self._set_buttons_state("disabled")

        def runner():
            try:
                work()
                result = (True, done_msg)
            except Exception as e:
                result = (False, f"เกิดข้อผิดพลาด: {e}")
            self.root.after(0, self._finish, result)

        threading.Thread(target=runner, daemon=True).start()

    def _finish(self, result):
        self.busy = False
        self._set_buttons_state("normal")
        self.refresh()
        ok, msg = result
        (messagebox.showinfo if ok else messagebox.showerror)("สถานะ", msg)

    def toggle_service(self):
        if is_running():
            self._background(stop_notifier, "หยุดระบบแจ้งเตือนแล้ว")
        else:
            self._background(start_notifier, "เริ่มระบบแจ้งเตือนในพื้นหลังแล้ว")

    def toggle_autostart(self):
        if autostart_enabled():
            self._background(remove_autostart, "ยกเลิกการเปิดพร้อม Windows แล้ว")
        else:
            def work():
                create_autostart()
                if not is_running():
                    start_notifier()

            self._background(work, "ตั้งค่าเปิดพร้อม Windows แล้ว และเริ่มระบบให้ทันที")

    # ---- สถานะ ------------------------------------------------------------
    def refresh(self):
        if is_running():
            self.lbl_status.config(
                text="สถานะ:  🟢 กำลังทำงาน (พร้อมแจ้งเตือน)", fg="#2E7D32"
            )
            self.btn_run.config(text="⏹ หยุดการทำงาน", bg="#D32F2F")
        else:
            self.lbl_status.config(text="สถานะ:  ⚪ หยุดทำงาน", fg="#757575")
            self.btn_run.config(text="▶ เริ่มทำงาน", bg="#4CAF50")
        if autostart_enabled():
            self.lbl_auto.config(
                text="เปิดพร้อม Windows:  ✔ เปิดใช้งาน", fg="#1976D2"
            )
            self.btn_auto.config(text="⚙ ยกเลิกเปิดพร้อม Windows", bg="#78909C")
        else:
            self.lbl_auto.config(text="เปิดพร้อม Windows:  ✖ ปิดอยู่", fg="#757575")
            self.btn_auto.config(text="⚙ ตั้งเปิดพร้อม Windows", bg="#607D8B")

    def _refresh_loop(self):
        if not self.busy:
            self.refresh()
        self.root.after(3000, self._refresh_loop)


def main():
    root = tk.Tk()
    ControlPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()

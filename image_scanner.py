# -*- coding: utf-8 -*-
"""
โมดูลสแกนรูปภาพตารางสอนด้วย AI (Gemini Vision)
รองรับทั้งการเลือกไฟล์รูปภาพ และการวางรูปจาก Clipboard (แคปหน้าจอ Win+Shift+S)
"""

import os
import sys
import io
import json
import base64
import requests
from PIL import Image, ImageGrab

if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
DATA_FILE = os.path.join(SCRIPT_DIR, "schedule_data.json")

DEFAULT_PERIODS_40MIN = [
    {"period": 1, "start": "08:20", "end": "09:00", "notify_at": None, "description": "คาบ 1 ไม่ต้องแจ้งเตือน"},
    {"period": 2, "start": "09:00", "end": "09:40", "notify_at": "08:20", "description": "แจ้งเตือนตอนต้นคาบ 1"},
    {"period": 3, "start": "09:40", "end": "10:20", "notify_at": "09:00", "description": "แจ้งเตือนตอนต้นคาบ 2"},
    {"period": 4, "start": "10:20", "end": "11:00", "notify_at": "09:40", "description": "แจ้งเตือนตอนต้นคาบ 3"},
    {"period": 5, "start": "11:00", "end": "11:40", "notify_at": "10:20", "description": "แจ้งเตือนตอนต้นคาบ 4"},
    {"period": 6, "start": "11:40", "end": "12:20", "notify_at": "11:00", "description": "แจ้งเตือนตอนต้นคาบ 5"},
    {"period": 7, "start": "12:20", "end": "13:00", "notify_at": "11:40", "description": "แจ้งเตือนตอนต้นคาบ 6"},
    {"period": 8, "start": "13:00", "end": "13:40", "notify_at": "12:20", "description": "แจ้งเตือนตอนต้นคาบ 7"},
    {"period": 9, "start": "13:40", "end": "14:20", "notify_at": "13:00", "description": "แจ้งเตือนตอนต้นคาบ 8"},
    {"period": 10, "start": "14:20", "end": "15:00", "notify_at": "13:40", "description": "แจ้งเตือนตอนต้นคาบ 9"},
]

EXTRACTION_PROMPT = """คุณคือผู้เชี่ยวชาญการอ่านตารางสอนของโรงเรียนไทย
กรุณาอ่านข้อมูลตารางสอนจากรูปภาพนี้ให้ครบถ้วนและแม่นยำ แล้วส่งกลับเป็น JSON ตามโครงสร้างนี้เท่านั้น:

{
  "school_name": "ชื่อโรงเรียน (ถ้าไม่พบใส่ โรงเรียนสารวิทยา)",
  "teacher_name": "ชื่อ-นามสกุลครู และชื่อเล่น (ถ้ามี)",
  "semester": "ภาคเรียนที่ ... ปีการศึกษา ...",
  "timetable": {
    "Monday": {
      "name_th": "วันจันทร์",
      "classes": {
        "1": { "subject": "รหัสวิชาหรือชื่อกิจกรรม เช่น พ22104", "class_room": "ระดับชั้น/ห้อง เช่น ม.2/10", "location": "สถานที่เรียน เช่น ศูนย์กีฬาชั้น 1 หรือ ห้อง 217" }
      }
    },
    "Tuesday": { "name_th": "วันอังคาร", "classes": {} },
    "Wednesday": { "name_th": "วันพุธ", "classes": {} },
    "Thursday": { "name_th": "วันพฤหัสบดี", "classes": {} },
    "Friday": { "name_th": "วันศุกร์", "classes": {} }
  }
}

ข้อกำหนดสำคัญ:
1. คาบไหนว่าง (ไม่มีสอน) ไม่ต้องใส่ใน "classes"
2. เลขคาบใน "classes" ให้ใช้ตัวเลขเป็น string เช่น "1", "2", "3", ..., "10" ตามคอลัมน์คาบในตาราง
3. หากในช่องมีรหัสวิชา (เช่น พ22104, พ30228, ว22104), เลขชั้น/ห้อง (เช่น 2/10 ให้ใส่เป็น ม.2/10, 6/5 ให้ใส่เป็น ม.6/5), และสถานที่ (เช่น ศูนย์กีฬาชั้น 1, 217 ให้ใส่ ห้อง 217) ให้แยกใส่แต่ละฟิลด์ให้ชัดเจน
4. ข้อควรระวังเรื่องฟอนต์วัยรุ่น/ฟอนต์น่ารักภาษาไทย: รหัสวิชาโรงเรียนมัธยมไทยจะขึ้นต้นด้วยอักษรย่อกลุ่มสาระภาษาไทยตามด้วยตัวเลข 5 หลักเสมอ (เช่น กลุ่มสาระสุขศึกษาและพลศึกษา ใช้ตัว 'พ' เช่น พ22104, พ30220, พ30228 ซึ่งบางฟอนต์ตัว พ อาจดูคล้ายตัว Y ในภาษาอังกฤษ ให้ใช้ตัวอักษรไทย 'พ' เสมอ)
5. หากเป็นกิจกรรม เช่น ลูกเสือ 2, ชมรม 2, หน้าที่พลเมือง 2, จริยธรรม 2 ให้ใส่ใน subject ส่วน class_room ใส่ระดับชั้น เช่น ม.2 และ location ใส่ '-' หากไม่ระบุ
"""


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"gemini_api_key": "", "gemini_model": "gemini-3.5-flash-lite"}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def image_to_base64_png(img_or_path):
    """แปลงไฟล์รูปภาพหรือ PIL Image เป็น base64 PNG พร้อมย่อขนาดถ้ารูปใหญ่เกินไปเพื่อให้ส่งไว"""
    if isinstance(img_or_path, str):
        img = Image.open(img_or_path)
    else:
        img = img_or_path

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    max_dim = 1600
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def grab_clipboard_image():
    """ดึงรูปภาพจาก Clipboard (ถ้าผู้ใช้กดแคปหน้าจอ Win+Shift+S หรือ Copy รูปไว้)"""
    data = ImageGrab.grabclipboard()
    if isinstance(data, Image.Image):
        return data
    if isinstance(data, list) and data:
        # กรณี Copy ไฟล์รูปภาพมาจาก Windows Explorer
        first = data[0]
        if isinstance(first, str) and os.path.isfile(first):
            return Image.open(first)
    return None


def scan_schedule_image(img_or_path, api_key=None):
    """ส่งรูปตารางสอนให้ Gemini Vision วิเคราะห์และคืนค่า dict โครงสร้าง schedule_data.json"""
    cfg = load_config()
    key = (api_key or cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise ValueError("ยังไม่ได้ตั้งค่า Gemini API Key")

    b64_img = image_to_base64_png(img_or_path)

    models_to_try = [cfg.get("gemini_model", "gemini-3.5-flash-lite"), "gemini-3.5-flash", "gemini-2.5-flash-lite"]
    last_error = None

    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        payload = {
            "contents": [{
                "parts": [
                    {"text": EXTRACTION_PROMPT},
                    {"inline_data": {"mime_type": "image/png", "data": b64_img}}
                ]
            }],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1
            }
        }
        try:
            resp = requests.post(url, params={"key": key}, json=payload, timeout=45)
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                continue
            res_json = resp.json()
            raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(raw_text)
            return build_full_schedule_data(parsed)
        except Exception as e:
            last_error = str(e)

    raise RuntimeError(f"ไม่สามารถวิเคราะห์รูปภาพได้: {last_error}")


def build_full_schedule_data(extracted):
    """รวมข้อมูลที่ AI สกัดได้เข้ากับโครงสร้างเวลาคาบละ 40 นาที"""
    existing = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    periods = existing.get("periods") or DEFAULT_PERIODS_40MIN
    day_defaults = {
        "Monday": "วันจันทร์",
        "Tuesday": "วันอังคาร",
        "Wednesday": "วันพุธ",
        "Thursday": "วันพฤหัสบดี",
        "Friday": "วันศุกร์",
    }

    raw_tt = extracted.get("timetable") or {}
    clean_tt = {}
    for day_en, day_th in day_defaults.items():
        day_obj = raw_tt.get(day_en) or {}
        raw_classes = day_obj.get("classes") or {}
        clean_classes = {}
        for p_key, c_info in raw_classes.items():
            if isinstance(c_info, dict) and c_info.get("subject"):
                clean_classes[str(p_key).strip()] = {
                    "subject": str(c_info.get("subject", "")).strip(),
                    "class_room": str(c_info.get("class_room", "-")).strip(),
                    "location": str(c_info.get("location", "-")).strip(),
                }
        clean_tt[day_en] = {
            "name_th": day_th,
            "classes": clean_classes
        }

    return {
        "school_name": extracted.get("school_name") or existing.get("school_name") or "โรงเรียนสารวิทยา",
        "teacher_name": extracted.get("teacher_name") or existing.get("teacher_name") or "คุณครู",
        "semester": extracted.get("semester") or existing.get("semester") or "ภาคเรียนที่ 2 ปีการศึกษา 2569",
        "period_duration_minutes": existing.get("period_duration_minutes", 40),
        "periods": periods,
        "timetable": clean_tt,
    }


def save_schedule_data(new_data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)

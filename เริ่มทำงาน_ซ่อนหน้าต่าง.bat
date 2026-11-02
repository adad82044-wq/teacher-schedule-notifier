@echo off
chcp 65001 > nul
cd /d "%~dp0"
start "" pyw teacher_notifier.pyw
echo เริ่มระบบแจ้งเตือนแล้ว (ถ้าทำงานอยู่แล้วจะไม่เปิดซ้ำ)
timeout /t 3 > nul

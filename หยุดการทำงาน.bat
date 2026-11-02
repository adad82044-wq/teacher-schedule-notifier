@echo off
chcp 65001 > nul
cd /d "%~dp0"
py -c "import control_panel as c; c.stop_notifier()"
echo หยุดระบบแจ้งเตือนแล้ว
timeout /t 3 > nul

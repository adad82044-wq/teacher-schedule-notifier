@echo off
chcp 65001 > nul
cd /d "%~dp0"
py -c "import control_panel as c; c.create_autostart(); c.is_running() or c.start_notifier(); print('OK')"
echo ติดตั้งเปิดพร้อม Windows แล้ว
timeout /t 5 > nul

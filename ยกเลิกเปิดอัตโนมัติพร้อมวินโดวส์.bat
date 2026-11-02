@echo off
chcp 65001 > nul
cd /d "%~dp0"
py -c "import control_panel as c; c.remove_autostart()"
echo ยกเลิกแล้ว
timeout /t 3 > nul

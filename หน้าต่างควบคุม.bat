@echo off
chcp 65001 > nul
cd /d "%~dp0"
start "" pyw control_panel.pyw

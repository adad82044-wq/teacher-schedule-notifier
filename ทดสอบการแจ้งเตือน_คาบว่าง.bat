@echo off
chcp 65001 > nul
cd /d "%~dp0"
start "" pyw teacher_notifier.pyw --test-free

@echo off
cd /d C:\Users\Administrator\fx_bot
py vps\daily_report.py >> logs\scheduler_daily_report.log 2>&1

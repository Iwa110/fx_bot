@echo off
title FX Dashboard Server
cd /d C:\Users\Administrator\fx_bot
python vps\dashboard_server.py >> logs\dashboard_server.log 2>&1

@echo off
start "Office Admin" cmd /k ^
cd /d C:\Users\MyUser\Documents\office-admin-1.1.0 && ^
start http://127.0.0.1:8000 && ^
uv run python main.py"

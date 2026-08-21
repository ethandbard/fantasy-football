@echo off
set DB_PATH=%~dp0data\fantasy.db
"%~dp0.venv-dashboard\Scripts\python.exe" -m shiny run gamedaybot/web/app.py --port 8001

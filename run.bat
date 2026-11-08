@echo off
cd /d "%~dp0"
if not exist venv (
  python -m venv venv
  venv\Scripts\pip install -r requirements.txt
)
venv\Scripts\python manage.py migrate --noinput
echo Starting on port 8000 - press Ctrl+C to stop.
venv\Scripts\python manage.py runserver 0.0.0.0:8000

#!/usr/bin/env bash
# Start the portal. Open http://localhost:8000  (other PCs: http://<this-pc-ip>:8000)
set -e
cd "$(dirname "$0")"
if [ ! -d venv ]; then
  python3 -m venv venv
  ./venv/bin/pip install -r requirements.txt
fi
./venv/bin/python manage.py migrate --noinput
echo "Starting on port 8000 - press Ctrl+C to stop."
./venv/bin/python manage.py runserver 0.0.0.0:8000

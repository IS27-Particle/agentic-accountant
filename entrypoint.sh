#!/bin/bash
Xvfb $DISPLAY -screen 0 $RESOLUTION &
sleep 1

fluxbox &
x11vnc -display $DISPLAY -nopw -listen localhost -shared -forever &
/usr/share/novnc/utils/launch.sh --vnc localhost:5900 --listen 6080 &
sleep 1

python3 /workspace/database.py
uvicorn web_app:app --host 0.0.0.0 --port 8080 &
sleep 2

python3 /workspace/orchestrator.py
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /workspace

RUN apt-get update && apt-get install -y software-properties-common \
    && add-apt-repository -y universe \
    && apt-get update && apt-get install -y \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    fluxbox \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir google-genai fastapi uvicorn requests python-multipart

EXPOSE 6080
EXPOSE 8080

ENV DISPLAY=:1
ENV RESOLUTION=1440x900x24

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

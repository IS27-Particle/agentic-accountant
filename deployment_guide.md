# Deployment Instructions
Follow these steps to deploy and run the Agentic Accountant system.
## 1. Directory Setup on Jupiter
Create the required storage directories on your host filesystem. This ensures browser sessions and data profiles persist between container rebuilds. Run these commands on Jupiter:
```bash
mkdir -p /Docs/Programming/GitHub/Agentic\ Accountant/user_session_data
mkdir -p /Docs/Programming/GitHub/Agentic\ Accountant/downloads

```
## 2. File Assembly
Ensure all files are placed in the root directory: /Docs/Programming/GitHub/Agentic Accountant/
 * modules_config.json
 * database.py
 * interpreter.py
 * orchestrator.py
 * web_app.py
 * Dockerfile
 * docker-compose.yml
 * entrypoint.sh
Ensure that entrypoint.sh has executable permissions:
```bash
chmod +x entrypoint.sh

```
## 3. Container Initialization
Build the image and launch the background services:
```bash
docker compose up -d --build

```
This command initializes the graphics display pipeline, creates the SQLite database, and launches the FastAPI web panel.
## 4. Initial Session Authentication
Connect to the browser stream to authenticate your target profiles.
 * Open a web browser on your network.
 * Navigate to the noVNC stream at http://jupiter:6080/vnc.html.
 * Inside the virtual environment, open a browser tab.
 * Log into Quicken Simplifi, Google Keep, Forma, and Amazon manually.
 * Complete any multi-factor authentication steps.
 * Close the browser tab.
The browser cookies and session storage elements are now written to your local user_session_data directory on Jupiter.
## 5. Scheduling Automation
Set up a daily cron job to trigger the data processing pipeline automatically at 3:00 AM every night. Open your host crontab configuration:
```bash
crontab -e

```
Add the following line to the file:
```text
0 3 * * * docker exec gemini-finance-agent python3 /workspace/orchestrator.py >> /var/log/accountant_pipeline.log 2>&1

```
The system will now run automatically in the background. It will use the saved browser sessions to sync your accounts and manage bills without prompting for credentials unless the cookies expire.

# Declarative Agentic Accountant

An advanced, self-healing AI financial reconciliation engine powered by Playwright browser automation and the Gemini 2.5 model suite. It automates financial ledger compilation, site synchronization (Monarch Money, YNAB, Quicken Simplifi, bank portals, benefits/HSA accounts), and analytical briefing generation while incorporating human-in-the-loop exception handling.

---

## Key Features

* 🛠️ **Declarative Automation**: Low-code browser sync pipelines defined entirely in simple JSON profiles (`modules_config.json`).
* 🧙 **Interactive Setup Wizard**: Dynamic step-based configuration dashboard (`/setup`) to add target budget portals and multiple email addresses.
* 🔒 **Password-Free Secure Sessions**: Playwright launches a headed Chromium instance allowing you to log in *once* manually. All session tokens and cookies are securely cached in local storage, eliminating the need to store passwords or Gmail app passwords.
* 🩺 **AI-Driven Self-Healing (Config & Code)**: When page layouts or selector configurations change, Gemini 2.5 Flash automatically diagnoses the traceback and heals the configuration parameters or python interpreter code logic on the fly.
* 🛡️ **Self-Healing Safeguards**: Built-in network inspection bypasses the AI self-healing pipeline during temporary network outages or DNS failures, preventing configuration corruption.
* 🔄 **Double-File Rollbacks**: Automatically creates backup files (`.bak`) before saving healed files. If pipeline re-execution fails after healing, it rolls back both the JSON config and the python code to their safe pre-healed states.
* 💬 **Interactive MFA Verification & Webhook Alerts**: Intercepts MFA/OTP challenges on portals, pauses execution, sends an alert notification directly to your Discord channel, and presents a verification input card on the dashboard control panel.
* 🤖 **Direct Agent Command Worker**: A background daemon polling loop monitors user commands submitted via the dashboard and executes sync instructions asynchronously.
* 📥 **Interactive Dashboards & CSV Exports**: Displays connected portals, active mapping rules, family briefings, transaction logs, and real-time execution terminals. Features one-click CSV downloads for rules and transactions.
* 🐳 **Docker-First & Dynamic Headless Automation**: Ready-to-go Docker configuration that dynamically determines browser headless states based on the active runtime environment.

---

## Tech Stack

* **Core Logic**: Python 3.x
* **Browser Automation**: Playwright (Chromium)
* **AI Model Engine**: Gemini 2.5 Flash (via `google-genai` SDK)
* **Web Dashboard**: FastAPI, Uvicorn, SQLite3
* **Aesthetics**: Glassmorphic dark mode styling using vanilla CSS

---

## Directory Structure

```text
├── database.py              # SQLite schema, dynamic migrations, and audits
├── interpreter.py           # Universal step-based browser engine & MFA handler
├── orchestrator.py          # Main execution loop, log teeing, and self-healing safeguards
├── web_app.py               # FastAPI dashboard, Setup Wizard, CSV exporter, and worker
├── modules_config.json      # Declarative browser sync profile configurations
├── docker-compose.yml       # Production deployment compose profile
├── Dockerfile               # Playwright-bundled python image build
├── entrypoint.sh            # Container boot orchestration
├── test_accounting.py       # Comprehensive unit test suite (31 test cases)
└── .env.template            # Workspace environment template
```

---

## Getting Started

### 1. Prerequisites
Ensure you have Python 3.10+ and Git installed on your system.

### 2. Environment Configuration
Copy the template file to `.env` and fill in your secrets:
```bash
cp .env.template .env
```

Review the `.env` settings (Notice that no email or bank passwords are required!):
```ini
# Gemini Credentials
GEMINI_API_KEY=your_gemini_api_key_here

# Control Panel Authentication (Optional)
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=supersecurepassword

# Discord Notifications Webhook (Optional)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your-id/your-token
```

### 3. Option A: Running Locally with Python
Set up a Python virtual environment and install dependencies:
```bash
python -m venv .venv
.venv/Scripts/activate      # On Windows
source .venv/bin/activate    # On Linux/macOS

pip install google-genai fastapi uvicorn requests python-multipart playwright
playwright install chromium
```

Start the Control Panel web server:
```bash
uvicorn web_app:app --reload --port 8000
```
Open [http://localhost:8000](http://localhost:8000) in your browser. 

Since no sites are configured yet, the app will automatically redirect you to the **Setup Wizard** where you can:
1. Enter your target budget portal URL.
2. Enter one or more email addresses.
3. Launch the browser to log in once manually and establish your secure session context.

Run the sync pipeline manually:
```bash
python orchestrator.py
```

### 4. Option B: Deploying with Docker Compose
To deploy in a containerized environment:
```bash
docker-compose up -d --build
```

---

## Verification & Tests

To execute the test suite (comprising 31 automated test cases verifying setup flow, headless routing, MFA alerts, and CSV exports):
```bash
python test_accounting.py
```

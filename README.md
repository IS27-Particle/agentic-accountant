# Autonomous Agentic Accounting System

An autonomous, self-healing agentic accounting engine powered by local LLM routing (Ollama: `qwen2.5-coder:14b`), double-entry book balancing, automated ledger ingestion, financial reporting, and browser synchronization.

---

## Key Features

* 🧠 **Local LLM Routing**: 100% autonomous inference routing to local Ollama (`http://10.0.0.25:11434`, model: `qwen2.5-coder:14b`) via `llm_router.py`. Configurable via environment variables (`OLLAMA_HOST`, `OLLAMA_MODEL`, `OLLAMA_ROUTINE_MODEL`, `OLLAMA_COMPLEX_MODEL`) with graceful fallback. Zero proprietary cloud locks.
* ⚖️ **Double-Entry Accounting Engine**: Complete chart of accounts (Assets, Liabilities, Equity, Revenue, Expenses), balanced journal entries, and automated trial balance validation.
* 📊 **Financial Reporting Generators**: Generates accurate, real-time Balance Sheet ($Assets = Liabilities + Equity$), Income Statement / P&L ($Net Income = Revenue - Expenses$), and Cash Flow Statements.
* 🩺 **Self-Healing Reconciliation**: Automated discrepancy detection, unposted transaction auto-ingestion, and comprehensive audit trail logging in SQLite (`shared_state.db`).
* 🛠️ **Declarative Automation**: Low-code browser sync pipelines defined in JSON profiles (`modules_config.json`).
* 🧙 **Interactive Setup Wizard**: Dynamic configuration dashboard (`/setup`) to configure budget portals and email sync.
* 🐳 **Docker-First Architecture**: Containerized deployment via Docker and Docker Compose.

---

## Directory Structure

```text
├── accounting_engine.py     # Core double-entry ledger, reports, and self-healing
├── llm_router.py            # Local Ollama LLM inference client & routing
├── database.py              # SQLite schema, migrations, charts of accounts, and audits
├── interpreter.py           # Universal step-based browser engine & MFA handler
├── orchestrator.py          # Main execution loop and self-healing orchestration
├── web_app.py               # FastAPI dashboard, Setup Wizard, and CSV exporter
├── modules_config.json      # Declarative browser sync profile configurations
├── docker-compose.yml       # Docker Compose profile
├── Dockerfile               # Playwright & FastAPI container definition
├── test_accounting.py       # Comprehensive unit & integration test suite
└── .env.template            # Workspace environment template
```

---

## Getting Started

### 1. Environment Configuration
```bash
cp .env.template .env
```

Environment variables:
```ini
# Local Ollama Configuration
OLLAMA_HOST=http://10.0.0.25:11434
OLLAMA_MODEL=qwen2.5-coder:14b

# Control Panel Authentication (Optional)
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=supersecurepassword
```

### 2. Running Verification Tests
Execute the comprehensive test suite covering double-entry balancing, classification, reporting, and reconciliation:
```bash
pytest -v test_accounting.py
```

# Architecture Specification
This document defines the system topology, relational schema mappings, and pipeline sequence flows for the Declarative Agentic Accountant.
## 1. System Topology
The platform operates as a single-container processing engine. It isolates the browser runtime, data store, and web dashboards from your host environment.
```text
[Scheduled Cron Execution]
          │
          ▼
   Orchestrator Engine ──> Persistent Browser Context
          │
          └──> Step Interpreter ──> JSON Step Configurations
                    │
                    ├──> Web Scraping Nodes (Simplifi, Keep, Forma, Amazon)
                    ├──> Secure IMAP Mail Fetcher
                    ├──> Gemini Reconciliation Reasoning (RAG)
                    └──> Automated Write-Back & Bill Linker Actions

```
 * Orchestrator Engine: Coordinates the daily task loops. It manages the runtime context, catches parsing exceptions, and executes the self-healing routines.
 * Step Interpreter: A universal execution library. It reads step lists from modules_config.json and maps action keys to programmatic actions.
 * Shared State Store: An embedded SQLite database containing raw ledger lists, mapping criteria, human review steps, and performance logs.
 * Persistent Browser Context: A Chromium workspace mounted directly to your host disk. It serializes cookie tables, encryption keys, and session parameters.
## 2. Relational Database Schemas
The state tracking database is structured into seven distinct tables inside shared_state.db.
 * raw_transactions: Deduplicates transaction lines harvested from bank profiles.
 * family_context: Stores unstructured metadata from notes and emails.
 * benefits_status: Tracks employer HSA balances and ledger status.
 * review_queue: Implements the human-in-the-loop state machine.
 * permanent_mapping_rules: Holds verified, deterministic routing rules.
 * daily_reports: Saves generated financial advisory briefings.
 * audit_log: Registers task completion milestones and self-healing events.
## 3. Declarative Execution Mechanics
Standard automation tools use procedural code files where selectors and flow logic are hardcoded. A single layout change on the bank webpage breaks the script and corrupts the runtime.
This platform uses a declarative design:
 * The modules_config.json configuration profile holds every CSS element selector, endpoint URL, timeout value, and execution order list.
 * The interpreter.py python execution layer is static and completely immutable. It translates the JSON configurations into actions.
 * When a front-end portal layout changes, the self-healing loop passes the error traceback and modules_config.json directly to Gemini 2.5 Flash.
 * Gemini updates only the specific selector strings inside the JSON file. It leaves the Python code untouched.
This separation of configuration from logic avoids syntax failures, module import conflicts, and memory caching issues during hot-reloads.
## 4. Write-Back and Bill Linkage Design
The platform executes live browser adjustments using two transactional loops.
 * Transaction Write-Back: The interpreter logs into Quicken Simplifi and opens the transaction ledger. It filters the local database for approved transaction IDs. For each matching transaction row on the page, it locates the checkmark element and performs a click to mark the item reviewed on the live server.
 * Bill Linkage: The interpreter opens the bills ledger in Quicken Simplifi. It matches upcoming unlinked bills against unlinked bank debits in the database with identical dollar values. It clicks the options dropdown next to the bill, selects the linking popup, inputs the transaction ID, checks the matching target row, and confirms the attachment on the live portal.

 * Proactive Export Scheduling: If automated scraping or direct connection fails, the system automatically flags the account for weekly manual export, generating an alert in the dashboard to guide you through the process.
46: * Dynamic Site Detection: The orchestrator monitors transaction classification failures, identifying new web portals that need to be added to the configuration, and uses Gemini to bootstrap the new scraping modules.
47: * MFA & Access Handling: The system detects MFA challenges and site blocks, pausing execution to request human intervention via the dashboard, ensuring credentials remain secure and sessions persistent.
 * ai_reasoning_log: Stores prompts and responses for AI-driven self-healing.
 * mfa_challenges: Tracks pending MFA requests for user intervention.
 * export_reminders: Manages weekly schedules for manual data exports.
 * task_requests: Holds natural language commands from the chat UI.
 * pending_new_sites: Identifies web pages needing new scraping modules.
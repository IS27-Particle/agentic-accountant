from fastapi import FastAPI, Form, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
import sqlite3
import os
import base64
import secrets
import csv
import io
import threading
import time
import orchestrator

# --- Local Dotenv Loader Utility ---
def load_dotenv(dotenv_path=".env"):
    if not os.path.exists(dotenv_path):
        return
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                elif v.startswith("'") and v.endswith("'"):
                    v = v[1:-1]
                os.environ[k] = v

load_dotenv()

def verify_credentials_optional(request: Request):
    correct_username = os.environ.get("DASHBOARD_USERNAME")
    correct_password = os.environ.get("DASHBOARD_PASSWORD")
    if not correct_username or not correct_password:
        return
        
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic realm='Agentic Accountant'"},
        )
    try:
        auth_type, encoded_credentials = auth_header.split(" ", 1)
        decoded = base64.b64decode(encoded_credentials).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic realm='Agentic Accountant'"},
        )
        
    is_correct_username = secrets.compare_digest(username, correct_username)
    is_correct_password = secrets.compare_digest(password, correct_password)
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic realm='Agentic Accountant'"},
        )

app = FastAPI(dependencies=[Depends(verify_credentials_optional)])

import database

# Get dynamic DB_PATH from database module
DB_PATH = database.DB_PATH

# --- Background Command Queue Worker ---
def command_worker_loop():
    # Wait for app start
    time.sleep(2)
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, command FROM command_queue WHERE status = 'PENDING' LIMIT 1")
            row = cursor.fetchone()
            if row:
                cmd_id, cmd_text = row
                print(f"Background worker: Processing command {cmd_id}: '{cmd_text}'")
                cursor.execute("UPDATE command_queue SET status = 'PROCESSING' WHERE id = ?", (cmd_id,))
                conn.commit()
                
                try:
                    orchestrator.run_pipeline()
                    cursor.execute("UPDATE command_queue SET status = 'SUCCESS' WHERE id = ?", (cmd_id,))
                except Exception as err:
                    print(f"Background worker error executing command: {err}")
                    cursor.execute("UPDATE command_queue SET status = 'FAILED' WHERE id = ?", (cmd_id,))
                conn.commit()
            conn.close()
        except Exception as ex:
            print(f"Background worker loop exception: {ex}")
        time.sleep(5)

import sys
import json

# Start background command polling loop
if "unittest" not in sys.modules and "pytest" not in sys.modules and "test_accounting" not in "".join(sys.argv):
    threading.Thread(target=command_worker_loop, daemon=True).start()

@app.get("/", response_class=HTMLResponse)
def dashboard():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Redirect to setup wizard if no budget portals are registered
    cursor.execute("SELECT COUNT(*) FROM budget_sites")
    if cursor.fetchone()[0] == 0:
        conn.close()
        return RedirectResponse(url="/setup", status_code=307)
    
    # Fetch latest report
    cursor.execute("SELECT report_content, timestamp FROM daily_reports ORDER BY id DESC LIMIT 1")
    latest_report = cursor.fetchone()
    
    # Fetch pending tasks
    cursor.execute("SELECT id, category, transaction_id, details, explanation, accept_changes, reject_changes FROM task_queue WHERE status = 'PENDING'")
    tasks = cursor.fetchall()
    
    # Fetch pending review_queue exceptions
    cursor.execute("SELECT transaction_id, date, payee, amount, user_input, proposed_rule, loop_state FROM review_queue WHERE loop_state != 'APPROVED'")
    review_items = cursor.fetchall()
    
    # Fetch permanent rules
    cursor.execute("SELECT id, keyword_pattern, target_category, target_bill_name FROM permanent_mapping_rules")
    rules = cursor.fetchall()
    
    # Stats
    cursor.execute("SELECT COUNT(*) FROM raw_transactions")
    total_tx = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM task_queue WHERE status = 'APPROVED'")
    reconciled_tx = cursor.fetchone()[0]
    pending_tx = len(tasks) + len(review_items)
    
    # Prune expired MFA challenges (older than 5 minutes)
    cursor.execute("""
        UPDATE mfa_challenges 
        SET status = 'EXPIRED' 
        WHERE status = 'PENDING' AND datetime(timestamp) < datetime('now', '-5 minutes')
    """)
    conn.commit()
    
    # Pending MFA challenges
    cursor.execute("SELECT id, site_name FROM mfa_challenges WHERE status = 'PENDING'")
    pending_mfa = cursor.fetchall()
    
    # Connected budget portals
    cursor.execute("SELECT site_name, url FROM budget_sites WHERE status = 'ACTIVE'")
    active_portals = cursor.fetchall()
    
    conn.close()
    
    # Start HTML layout
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agentic Accountant Control Panel</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(22, 28, 45, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --accent-purple: #8b5cf6;
            --accent-blue: #3b82f6;
            --accent-emerald: #10b981;
            --accent-rose: #f43f5e;
            --glass-shine: rgba(255, 255, 255, 0.03);
        }
        
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        
        body {
            background-color: var(--bg-color);
            color: var(--text-primary);
            font-family: 'Outfit', sans-serif;
            line-height: 1.6;
            padding: 40px 20px;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(139, 92, 246, 0.05) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(59, 130, 246, 0.05) 0%, transparent 40%);
            background-attachment: fixed;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        header {
            margin-bottom: 40px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 20px;
        }
        
        h1 {
            font-size: 2.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, #a78bfa 0%, #60a5fa 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .status-badge {
            background: rgba(16, 185, 129, 0.1);
            color: var(--accent-emerald);
            border: 1px solid rgba(16, 185, 129, 0.2);
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 0.9rem;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            background-color: var(--accent-emerald);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 10px var(--accent-emerald);
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }

        .stat-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(12px);
            position: relative;
            overflow: hidden;
            transition: all 0.3s ease;
        }

        .stat-card:hover {
            transform: translateY(-4px);
            border-color: rgba(139, 92, 246, 0.3);
        }

        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 100%;
            background: linear-gradient(to bottom, var(--glass-shine), transparent);
            pointer-events: none;
        }

        .stat-card h3 {
            font-size: 1rem;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 8px;
        }

        .stat-card .value {
            font-size: 2.2rem;
            font-weight: 700;
        }

        .stat-card.purple .value { color: #c084fc; }
        .stat-card.blue .value { color: #60a5fa; }
        .stat-card.emerald .value { color: #34d399; }

        .dashboard-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 30px;
        }

        @media (max-width: 900px) {
            .dashboard-grid {
                grid-template-columns: 1fr;
            }
        }

        .section-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 30px;
            backdrop-filter: blur(12px);
            margin-bottom: 30px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
        }

        .section-card h2 {
            font-size: 1.5rem;
            font-weight: 600;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
            border-left: 4px solid var(--accent-purple);
            padding-left: 12px;
        }

        /* Forms & Inputs */
        form {
            display: flex;
            gap: 12px;
            width: 100%;
        }

        input[type="text"] {
            flex-grow: 1;
            background: rgba(17, 24, 39, 0.8);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            padding: 14px 18px;
            border-radius: 10px;
            font-family: inherit;
            font-size: 1rem;
            transition: all 0.2s ease;
        }

        input[type="text"]:focus {
            outline: none;
            border-color: var(--accent-purple);
            box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.2);
        }

        button[type="submit"] {
            background: linear-gradient(135deg, var(--accent-purple) 0%, #7c3aed 100%);
            color: white;
            border: none;
            padding: 14px 28px;
            border-radius: 10px;
            font-family: inherit;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            box-shadow: 0 4px 12px rgba(139, 92, 246, 0.2);
        }

        button[type="submit"]:hover {
            opacity: 0.95;
            transform: translateY(-1px);
            box-shadow: 0 6px 16px rgba(139, 92, 246, 0.3);
        }

        /* Briefing content */
        .briefing-box {
            font-size: 1.05rem;
            color: #d1d5db;
            line-height: 1.8;
        }

        .briefing-box strong {
            display: block;
            margin-bottom: 12px;
            font-size: 0.9rem;
            color: var(--text-secondary);
            font-family: 'JetBrains Mono', monospace;
        }

        /* Queue item styles */
        .queue-item {
            background: rgba(17, 24, 39, 0.4);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 15px;
            transition: all 0.2s ease;
        }

        .queue-item:hover {
            border-color: rgba(255, 255, 255, 0.15);
        }

        .queue-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 15px;
        }

        .queue-meta {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .tx-payee {
            font-size: 1.15rem;
            font-weight: 700;
        }

        .tx-amount {
            font-size: 1.15rem;
            font-weight: 700;
            color: var(--accent-emerald);
        }

        .tx-date {
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-secondary);
            font-size: 0.9rem;
        }

        .badge-state {
            background: rgba(245, 158, 11, 0.1);
            color: #f59e0b;
            border: 1px solid rgba(245, 158, 11, 0.2);
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
            text-transform: uppercase;
        }
        
        .badge-state.proposed {
            background: rgba(59, 130, 246, 0.1);
            color: var(--accent-blue);
            border-color: rgba(59, 130, 246, 0.2);
        }

        .badge-state.rejected {
            background: rgba(244, 63, 94, 0.1);
            color: var(--accent-rose);
            border-color: rgba(244, 63, 94, 0.2);
        }

        .proposed-rule-box {
            background: rgba(31, 41, 55, 0.5);
            border: 1px solid var(--border-color);
            padding: 12px 16px;
            border-radius: 8px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            margin-bottom: 15px;
            color: #c084fc;
        }

        .action-buttons {
            display: flex;
            gap: 10px;
        }

        .btn-action {
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.9rem;
            text-decoration: none;
            text-align: center;
            display: inline-block;
            transition: all 0.2s ease;
        }

        .btn-action.approve {
            background: var(--accent-emerald);
            color: #ffffff;
            box-shadow: 0 4px 10px rgba(16, 185, 129, 0.2);
        }

        .btn-action.approve:hover {
            opacity: 0.9;
            transform: translateY(-1px);
        }

        .btn-action.reject {
            background: var(--accent-rose);
            color: #ffffff;
            box-shadow: 0 4px 10px rgba(244, 63, 94, 0.2);
        }

        .btn-action.reject:hover {
            opacity: 0.9;
            transform: translateY(-1px);
        }

        /* Rules Table */
        .rules-table-wrapper {
            overflow-x: auto;
            border-radius: 12px;
            border: 1px solid var(--border-color);
        }

        .rules-table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.95rem;
            background: rgba(17, 24, 39, 0.2);
        }

        .rules-table th {
            background: rgba(17, 24, 39, 0.6);
            color: var(--text-secondary);
            font-weight: 600;
            padding: 14px 18px;
            text-transform: uppercase;
            font-size: 0.8rem;
            letter-spacing: 0.05em;
            border-bottom: 1px solid var(--border-color);
        }

        .rules-table td {
            padding: 14px 18px;
            border-bottom: 1px solid var(--border-color);
            color: #d1d5db;
        }

        .rules-table tr:last-child td {
            border-bottom: none;
        }

        .rule-pattern {
            font-family: 'JetBrains Mono', monospace;
            color: var(--accent-purple);
            background: rgba(139, 92, 246, 0.1);
            padding: 2px 6px;
            border-radius: 4px;
        }

        .rule-category {
            font-weight: 600;
            color: var(--text-primary);
        }

        .rule-bill {
            color: var(--text-secondary);
        }
        
        .no-records {
            text-align: center;
            color: var(--text-secondary);
            padding: 30px;
            font-style: italic;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>Agentic Accountant</h1>
                <p style="color: var(--text-secondary); font-size: 1.1rem; margin-top: 4px;">Declarative Financial Reconciliation Engine</p>
                <a href="/db-viewer" style="color: var(--accent-purple); text-decoration: none; font-weight: 600; font-size: 1.05rem; margin-top: 8px; display: inline-flex; align-items: center; gap: 6px;">📁 Live Table Viewer →</a>
            </div>
            <div class="status-badge">
                <span class="status-dot"></span> System Live & Monitoring
            </div>
        </header>
"""
    # Render MFA alert banner if active challenges are pending
    if pending_mfa:
        for chal_id, site_name in pending_mfa:
            html += f"""
        <div style="background: rgba(239, 68, 68, 0.2); border: 1px solid #ef4444; color: #ffffff; padding: 20px; border-radius: 12px; margin-bottom: 25px;">
            <h3 style="margin-top: 0; color: #f87171;">MFA Verification Required</h3>
            <p>The system requires verification for <strong>{site_name}</strong> to continue processing.</p>
            <form action="/submit-mfa" method="post" style="display: flex; gap: 12px; align-items: center; margin-top: 15px;">
                <input type="hidden" name="challenge_id" value="{chal_id}" />
                <input type="text" name="mfa_code" placeholder="Enter MFA Verification Code" required style="padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border-color); background: #1f2937; color: white; outline: none; width: 250px;" />
                <button type="submit" style="background: var(--accent-emerald); color: white; border: none; padding: 10px 20px; border-radius: 8px; font-weight: 600; cursor: pointer; transition: background 0.2s;">Verify & Resume</button>
            </form>
        </div>
        """

    html += """
        <div class="stats-grid">
            <div class="stat-card purple">
                <h3>Total Scraped Transactions</h3>
                <div class="value">""" + str(total_tx) + """</div>
                <a href="/export/transactions" style="font-size: 0.8rem; color: #10b981; text-decoration: none; position: absolute; bottom: 10px; right: 15px;">Download CSV</a>
            </div>
            <div class="stat-card blue">
                <h3>Pending Exceptions</h3>
                <div class="value">""" + str(pending_tx) + """</div>
            </div>
            <div class="stat-card emerald">
                <h3>Reconciled Rules</h3>
                <div class="value">""" + str(reconciled_tx) + """</div>
            </div>
        </div>

        <div class="dashboard-grid">
            <!-- Left Column -->
            <div class="main-column">
                <!-- Daily Financial Briefing -->
                <div class="section-card">
                    <h2>Daily Financial Briefing</h2>
                    <div class="briefing-box">"""
    
    if latest_report:
        html += "<strong>Generated: " + str(latest_report[1]) + "</strong><p>" + str(latest_report[0]).replace("\n", "<br/>") + "</p>"
    else:
        html += "<p class='no-records'>No analytical briefings found in database registries.</p>"
        
    html += """</div>
                </div>

                <!-- Reconciliation Exception Queue -->
                <div class="section-card">
                    <h2>Reconciliation Exception Queue</h2>"""
    
    if not tasks and not review_items:
        html += "<p class='no-records' style='color: var(--accent-emerald); font-weight: 600;'>✓ All task queue items resolved.</p>"
    else:
        # Render review_items (legacy)
        for tx_id, dt, payee, amount, user_input, proposed_rule, loop_state in review_items:
            state_label = "Pending Input" if loop_state == "PENDING_INPUT" else ("Rule Proposed" if loop_state == "RULE_PROPOSED" else "Rejected")
            html += f"""
            <div class='queue-item'>
                <div class='queue-header'>
                    <span class='tx-payee' style='color: var(--accent-purple); font-size: 1.1rem;'>{payee}</span>
                    <span class='badge-state proposed'>{state_label}</span>
                </div>
                <div style='margin-bottom: 12px; font-size: 0.95rem; color: #d1d5db;'>Date: {dt} | Amount: ${amount:.2f}</div>
            """
            if loop_state in ("PENDING_INPUT", "REJECTED"):
                placeholder = "Provide context to re-draft this rule..." if loop_state == "REJECTED" else "Provide context..."
                html += f"""
                <form action='/submit-input' method='post' style="display: inline-flex; gap: 10px; width: auto; align-items: center;">
                    <input type="hidden" name="tx_id" value="{tx_id}" />
                    <input type="text" name="user_text" placeholder="{placeholder}" required style="padding: 6px 12px; font-size:0.9rem; border-radius: 6px; width: 200px; background:#111827; border: 1px solid var(--border-color); color:white;" />
                    <button type="submit" class="btn-action approve" style="padding: 6px 16px; font-size: 0.85rem; border: none; cursor: pointer;">Submit context</button>
                </form>
                """
            elif loop_state == "RULE_PROPOSED":
                html += f"""
                <div class='proposed-rule-box' style='font-size:0.85rem; border-left: 3px solid var(--accent-emerald); background: rgba(16, 185, 129, 0.05); margin-bottom:15px; color:#34d399;'>
                    <strong>Proposed rule:</strong> {proposed_rule}
                </div>
                <div style="display: flex; gap: 10px;">
                    <a href="/action-rule?tx_id={tx_id}&action=approve" class="btn-action approve" style="text-decoration: none; padding: 6px 16px; font-size: 0.85rem; border-radius: 6px; text-align: center; display: inline-block;">Approve</a>
                    <a href="/action-rule?tx_id={tx_id}&action=reject" class="btn-action reject" style="text-decoration: none; padding: 6px 16px; font-size: 0.85rem; border-radius: 6px; text-align: center; display: inline-block;">Reject</a>
                </div>
                """
            html += "</div>"

        # Render tasks (new)
        for t_id, cat, tx_id, details_json, explanation, accept_desc, reject_desc in tasks:
            details = json.loads(details_json) if details_json else {}
            tx_details = ""
            if "transaction" in details:
                t = details["transaction"]
                tx_details = f"<div style='font-size:0.85rem; background:rgba(0,0,0,0.2); padding:8px; border-radius:6px; margin-bottom:10px;'><strong style='color:var(--accent-purple);'>Transaction:</strong> Date: {t.get('date')} | Payee: {t.get('payee')} | Amount: ${t.get('amount')} | Category: {t.get('category')} | Account: {t.get('account')}</div>"
            elif "source_transaction" in details:
                s = details["source_transaction"]
                t = details.get("target_transaction") or {}
                tx_details = f"<div style='font-size:0.85rem; background:rgba(0,0,0,0.2); padding:8px; border-radius:6px; margin-bottom:10px;'><strong style='color:var(--accent-blue);'>Source:</strong> Date: {s.get('date')} | Payee: {s.get('payee')} | Amount: ${s.get('amount')} | Category: {s.get('category')} | Account: {s.get('account')}<br/><strong style='color:var(--accent-purple);'>Target:</strong> Date: {t.get('date')} | Payee: {t.get('payee')} | Amount: {t.get('amount')} | Category: {t.get('category')} | Account: {t.get('account')}</div>"
                
            html += f"""
            <div class='queue-item'>
                <div class='queue-header'>
                    <span class='tx-payee' style='color: var(--accent-purple); font-size: 1.1rem;'>{cat}</span>
                    <span class='badge-state proposed'>Pending Action</span>
                </div>
                <div style='margin-bottom: 12px; font-size: 0.95rem; color: #d1d5db;'>{explanation}</div>
                {tx_details}
                
                <div class='proposed-rule-box' style='font-size:0.85rem; border-left: 3px solid var(--accent-emerald); background: rgba(16, 185, 129, 0.05); margin-bottom:10px; color:#34d399;'>
                    <strong>Accept:</strong> {accept_desc}
                </div>
                <div class='proposed-rule-box' style='font-size:0.85rem; border-left: 3px solid var(--accent-rose); background: rgba(244, 63, 94, 0.05); margin-bottom:15px; color:#f43f5e;'>
                    <strong>Reject:</strong> {reject_desc}
                </div>
            """
            
            # Form for actions
            html += f"""
            <form action="/task-action" method="post" style="display: inline-flex; gap: 10px; width: auto; align-items: center;">
                <input type="hidden" name="task_id" value="{t_id}" />
            """
            
            # Show input text field if context input is needed
            if cat in ("New Payee", "Unknown Retailer", "New Category", "Re-Authentication Required", "New Retailer"):
                html += f"""
                <input type="text" name="user_text" placeholder="Provide input..." required style="padding: 6px 12px; font-size:0.9rem; border-radius: 6px; width: 200px; background:#111827; border: 1px solid var(--border-color); color:white;" />
                """
                
            html += """
                <button type="submit" name="action" value="approve" class="btn-action approve" style="padding: 6px 16px; font-size: 0.85rem; border: none; cursor: pointer;">Accept</button>
                <button type="submit" name="action" value="reject" class="btn-action reject" style="padding: 6px 16px; font-size: 0.85rem; border: none; cursor: pointer;">Reject</button>
            </form>
            </div>
            """

    html += """</div>
            </div>

            <!-- Right Column -->
            <div class="sidebar-column">
                <!-- Direct Agent Command -->
                <div class="section-card">
                    <h2>Direct Agent Command</h2>
                    <form action="/send-command" method="post" style="flex-direction: column; gap: 15px;">
                        <input type="text" name="command" placeholder="e.g. Sync Simplifi and Forma HSA..." required style="width: 100%;"/>
                        <button type="submit" style="width: 100%;">Execute Command</button>
                    </form>
                </div>

                <!-- Connected Portals -->
                <div class="section-card">
                    <h2>Connected Portals</h2>
                    <div style="display: flex; flex-direction: column; gap: 10px;">"""
    for p_name, p_url in active_portals:
        display_name = p_name.replace("_sync", "").replace("_", " ").title()
        html += f"""
                        <div style="padding: 10px; background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 8px; display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <strong style="color: var(--accent-purple); font-size: 0.95rem;">{display_name}</strong>
                                <div style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 2px; font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px;">{p_url}</div>
                            </div>
                            <span class="status-badge" style="padding: 2px 8px; font-size: 0.75rem; background: rgba(16, 185, 129, 0.2); color: #34d399; border-radius: 4px; border: 1px solid rgba(16, 185, 129, 0.3);">Active</span>
                        </div>"""
    if not active_portals:
        html += "<p class='no-records'>No active portals connected.</p>"
    html += """
                    </div>
                    <div style="margin-top: 15px; text-align: center;">
                        <a href="/setup" style="font-size: 0.9rem; color: var(--accent-purple); text-decoration: none; font-weight: 600;">Manage Portals & Emails →</a>
                    </div>
                </div>

                <!-- Active Mapping Rules -->
                <div class="section-card">
                    <h2 style="display: flex; justify-content: space-between; align-items: center;"><span>Active Mapping Rules</span><a href="/export/rules" style="font-size: 0.9rem; color: #10b981; text-decoration: none;">Download CSV</a></h2>
                    <div class="rules-table-wrapper">"""
    
    if not rules:
        html += "<p class='no-records'>No active mapping rules configured.</p>"
    else:
        html += "        <table class='rules-table'>"
        html += "            <thead>"
        html += "                <tr>"
        html += "                    <th>Pattern</th>"
        html += "                    <th>Category</th>"
        html += "                    <th>Biller</th>"
        html += "                </tr>"
        html += "            </thead>"
        html += "            <tbody>"
        for _, pat, cat, bil in rules:
            html += "                <tr>"
            html += "                    <td><span class='rule-pattern'>" + str(pat) + "</span></td>"
            html += "                    <td><span class='rule-category'>" + str(cat) + "</span></td>"
            html += "                    <td><span class='rule-bill'>" + str(bil or 'None') + "</span></td>"
            html += "                    </tr>"
        html += "            </tbody>"
        html += "        </table>"

    html += """</div>
                </div>
            </div>
        </div>
        <!-- System Execution Logs -->
        <div class="section-card" style="margin-top: 30px;">
            <h2>System Execution Logs</h2>
            <div id="logs-container" style="background: #060913; border: 1px solid var(--border-color); border-radius: 12px; padding: 20px; font-family: 'JetBrains Mono', monospace; font-size: 0.9rem; color: #34d399; height: 300px; overflow-y: auto; white-space: pre-wrap;">
                Loading execution logs...
            </div>
            <script>
                function fetchLogs() {
                    fetch('/logs')
                        .then(response => response.text())
                        .then(html => {
                            const container = document.getElementById('logs-container');
                            const isAtBottom = container.scrollHeight - container.clientHeight <= container.scrollTop + 10;
                            container.innerHTML = html;
                            if (isAtBottom) {
                                container.scrollTop = container.scrollHeight;
                            }
                        });
                }
                fetchLogs();
                setInterval(fetchLogs, 3000);
            </script>
        </div>
    </div>
</body>
</html>"""
    
    return html

@app.post("/submit-input")
def handle_input(tx_id: str = Form(...), user_text: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE review_queue SET user_input = ?, loop_state = 'INPUT_PROVIDED' WHERE transaction_id = ?", (user_text, tx_id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.get("/action-rule")
def handle_rule_action(tx_id: str, action: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if action == "approve":
        cursor.execute("SELECT proposed_rule FROM review_queue WHERE transaction_id = ?", (tx_id,))
        rule_row = cursor.fetchone()
        if rule_row and rule_row[0]:
            try:
                # Strip potential markdown code block wrappers
                proposed_text = rule_row[0].strip()
                if proposed_text.startswith("```"):
                    lines = proposed_text.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    proposed_text = "\n".join(lines).strip()
                
                # Parse: Pattern: Walmart | Category: Groceries | Bill: Walmart Bill
                parts = {}
                for item in proposed_text.split(" | "):
                    if ": " in item:
                        k, v = item.split(": ", 1)
                        parts[k.strip()] = v.strip()
                
                # Check required parts
                pattern = parts.get("Pattern")
                category = parts.get("Category")
                bill = parts.get("Bill") or ""
                
                if pattern and category:
                    cursor.execute("INSERT INTO permanent_mapping_rules (keyword_pattern, target_category, target_bill_name) VALUES (?, ?, ?)", (pattern, category, bill))
                    cursor.execute("UPDATE review_queue SET loop_state = 'APPROVED' WHERE transaction_id = ?", (tx_id,))
                else:
                    print(f"Warning: proposed rule formatting missing pattern or category: {rule_row[0]}")
            except Exception as parse_err:
                print(f"Failed parsing proposed rule '{rule_row[0]}': {parse_err}")
                # Fallback simple insert if splitting failed but it's approval time
                cursor.execute("INSERT INTO permanent_mapping_rules (keyword_pattern, target_category, target_bill_name) VALUES (?, 'Reconciled', '')", (tx_id, ))
                cursor.execute("UPDATE review_queue SET loop_state = 'APPROVED' WHERE transaction_id = ?", (tx_id,))
    elif action == "reject":
        cursor.execute("UPDATE review_queue SET loop_state = 'REJECTED', user_input = NULL, proposed_rule = NULL WHERE transaction_id = ?", (tx_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.post("/send-command")
def send_command(command: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO command_queue (command, status) VALUES (?, 'PENDING')", (command,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.get("/logs", response_class=HTMLResponse)
def get_logs():
    log_path = "orchestrator.log"
    if os.path.exists("/workspace"):
        log_path = "/workspace/orchestrator.log"
    if not os.path.exists(log_path):
        return "<p class='no-records'>No execution logs found yet.</p>"
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        last_lines = lines[-50:]
        import html
        escaped_lines = [html.escape(line) for line in last_lines]
        return "<pre style='margin: 0; font-family: inherit; color: inherit;'>" + "".join(escaped_lines) + "</pre>"
    except Exception as e:
        return f"<p class='no-records'>Error reading logs: {e}</p>"

@app.post("/submit-mfa")
def submit_mfa(challenge_id: int = Form(...), mfa_code: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE mfa_challenges SET code = ?, status = 'RESOLVED' WHERE id = ?", (mfa_code, challenge_id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.get("/export/transactions")
def export_transactions():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, date, payee, amount, category, account, writeback_status FROM raw_transactions")
    rows = cursor.fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Date", "Payee", "Amount", "Category", "Account", "Writeback Status"])
    for r in rows:
        writer.writerow(r)
        
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions_export.csv"}
    )

@app.get("/export/rules")
def export_rules():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, keyword_pattern, target_category, target_bill_name FROM permanent_mapping_rules")
    rows = cursor.fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Keyword Pattern", "Target Category", "Target Bill Name"])
    for r in rows:
        writer.writerow(r)
        
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mapping_rules_export.csv"}
    )

@app.post("/task-action")
def handle_task_action(task_id: int = Form(...), action: str = Form(...), user_text: str = Form(None)):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT category, transaction_id, details, explanation, accept_changes, reject_changes FROM task_queue WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return RedirectResponse(url="/", status_code=303)
            
        category, tx_id, details_json, explanation, accept_desc, reject_desc = row
        details = json.loads(details_json) if details_json else {}
        
        if action == "approve":
            # Execute accept logic depending on category
            if category == "Bill Attachment":
                cursor.execute("UPDATE raw_transactions SET writeback_status = 'SUCCESS' WHERE id = ?", (tx_id,))
                database.log_change_audit(conn, "Quicken Simplifi", "BILL_ATTACH", f"Attached bill: {accept_desc}", transaction_id=tx_id, task_id=task_id)
            elif category == "Bill Addition":
                payee = details.get("transaction", {}).get("payee")
                if payee:
                    cursor.execute("INSERT INTO permanent_mapping_rules (keyword_pattern, target_category, target_bill_name) VALUES (?, 'Bills', ?)", (payee, payee))
                    rule_id = cursor.lastrowid
                    database.log_change_audit(conn, "Rule Mapping", "RULE_CREATE", f"Created bill addition rule: {payee}", rule_id=rule_id, transaction_id=tx_id, task_id=task_id)
            elif category == "Transfer Attachment":
                cursor.execute("UPDATE raw_transactions SET writeback_status = 'SUCCESS' WHERE id = ?", (tx_id,))
                recip_id = details.get("target_transaction", {}).get("id")
                if recip_id:
                    cursor.execute("UPDATE raw_transactions SET writeback_status = 'SUCCESS' WHERE id = ?", (recip_id,))
                database.log_change_audit(conn, "Quicken Simplifi", "TRANSFER_LINK", f"Linked transfer source {tx_id} and target {recip_id}", transaction_id=tx_id, task_id=task_id)
            elif category in ("New Payee", "Unknown Retailer", "New Category"):
                input_val = user_text or "General"
                if category == "New Payee":
                    cursor.execute("UPDATE raw_transactions SET payee = ? WHERE id = ?", (input_val, tx_id))
                    database.log_change_audit(conn, "Quicken Simplifi", "PAYEE_UPDATE", f"Updated payee to {input_val}", transaction_id=tx_id, task_id=task_id)
                elif category == "New Category":
                    cursor.execute("UPDATE raw_transactions SET category = ? WHERE id = ?", (input_val, tx_id))
                    payee = details.get("transaction", {}).get("payee")
                    if payee:
                        cursor.execute("INSERT INTO permanent_mapping_rules (keyword_pattern, target_category) VALUES (?, ?)", (payee, input_val))
                    database.log_change_audit(conn, "Rule Mapping", "RULE_CREATE", f"Created category rule: {payee} -> {input_val}", transaction_id=tx_id, task_id=task_id)
            elif category == "Expense Split":
                cursor.execute("UPDATE raw_transactions SET category = 'Split / Mixed' WHERE id = ?", (tx_id,))
                database.log_change_audit(conn, "Quicken Simplifi", "TRANSACTION_SPLIT", f"Split expense: {accept_desc}", transaction_id=tx_id, task_id=task_id)
            elif category == "Personal Payment":
                cursor.execute("UPDATE raw_transactions SET category = 'Transfer (no account)' WHERE id = ?", (tx_id,))
                database.log_change_audit(conn, "Quicken Simplifi", "PERSONAL_PAYMENT", "Mapped personal payment to Transfer (no account)", transaction_id=tx_id, task_id=task_id)
            elif category == "Bank Payment":
                cursor.execute("UPDATE raw_transactions SET category = 'Interest Income' WHERE id = ?", (tx_id,))
                database.log_change_audit(conn, "Quicken Simplifi", "BANK_PAYMENT", "Mapped interest payment to Interest Income", transaction_id=tx_id, task_id=task_id)
            else:
                cursor.execute("UPDATE raw_transactions SET writeback_status = 'SUCCESS' WHERE id = ?", (tx_id,))
                database.log_change_audit(conn, "Core Process", "TASK_RESOLVE", f"Resolved task {task_id}: {category}", transaction_id=tx_id, task_id=task_id)
                
            cursor.execute("UPDATE task_queue SET status = 'APPROVED' WHERE id = ?", (task_id,))
            
        elif action == "reject":
            database.log_change_audit(conn, "Core Process", "TASK_REJECT", f"Rejected task {task_id}: {category}", transaction_id=tx_id, task_id=task_id)
            cursor.execute("UPDATE task_queue SET status = 'REJECTED' WHERE id = ?", (task_id,))
            
        conn.commit()
        return RedirectResponse(url="/", status_code=303)
    finally:
        conn.close()

def get_safe_db_path(db_name: str):
    db_name = os.path.basename(db_name)
    if not db_name.endswith(".db"):
        raise HTTPException(status_code=400, detail="Invalid database extension")
    base_dir = os.path.dirname(os.path.abspath(DB_PATH))
    full_path = os.path.join(base_dir, db_name)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Database file not found")
    return full_path

@app.get("/db-viewer/api/databases")
def db_viewer_databases():
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    dbs = [f for f in os.listdir(db_dir) if f.endswith(".db")]
    return dbs

@app.get("/db-viewer/api/tables")
def db_viewer_tables(db: str):
    db_path = get_safe_db_path(db)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")]
        return tables
    finally:
        conn.close()

@app.get("/db-viewer/api/data")
def db_viewer_data(db: str, table: str):
    db_path = get_safe_db_path(db)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        
        # Get columns
        cursor.execute(f"PRAGMA table_info('{table}')")
        cols_raw = cursor.fetchall()
        columns = []
        pk_col = None
        for c in cols_raw:
            columns.append({
                "name": c["name"],
                "type": c["type"],
                "pk": c["pk"] > 0
            })
            if c["pk"] > 0:
                pk_col = c["name"]
                
        if not pk_col and columns:
            pk_col = columns[0]["name"]
            
        # Get foreign keys list
        cursor.execute(f"PRAGMA foreign_key_list('{table}')")
        fks_raw = cursor.fetchall()
        fks = {}
        for f in fks_raw:
            fks[f["from"]] = {
                "table": f["table"],
                "to": f["to"]
            }
            
        # Get all rows
        cursor.execute(f"SELECT * FROM '{table}'")
        rows_raw = cursor.fetchall()
        rows = []
        
        fk_choices = {}
        
        for r in rows_raw:
            row_dict = {}
            for c in columns:
                col_name = c["name"]
                val = r[col_name]
                
                if col_name in fks:
                    fk_info = fks[col_name]
                    ref_table = fk_info["table"]
                    ref_to = fk_info["to"]
                    
                    # Fetch target row if FK has value
                    ref_row = None
                    if val is not None:
                        cursor.execute(f"SELECT * FROM '{ref_table}' WHERE {ref_to} = ?", (val,))
                        ref_row_raw = cursor.fetchone()
                        if ref_row_raw:
                            ref_row = dict(ref_row_raw)
                            
                    row_dict[col_name] = {
                        "value": val,
                        "is_fk": True,
                        "ref_table": ref_table,
                        "ref_to": ref_to,
                        "resolved": ref_row
                    }
                    
                    if ref_table not in fk_choices:
                        cursor.execute(f"SELECT * FROM '{ref_table}'")
                        fk_choices[ref_table] = [dict(choice) for choice in cursor.fetchall()]
                else:
                    row_dict[col_name] = {
                        "value": val,
                        "is_fk": False
                    }
            rows.append(row_dict)
            
        return {
            "columns": columns,
            "pk_col": pk_col,
            "fks": fks,
            "rows": rows,
            "fk_choices": fk_choices
        }
    finally:
        conn.close()

@app.post("/db-viewer/api/edit")
def db_viewer_edit(
    db: str = Form(...),
    table: str = Form(...),
    pk_col: str = Form(...),
    pk_val: str = Form(...),
    col: str = Form(...),
    val: str = Form(...)
):
    db_path = get_safe_db_path(db)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        
        cursor.execute(f"PRAGMA table_info('{table}')")
        cols = cursor.fetchall()
        col_type = "TEXT"
        for c in cols:
            if c[1] == col:
                col_type = c[2].upper()
                break
                
        validated_val = val
        if val == "None" or val == "":
            validated_val = None
        else:
            if "INT" in col_type:
                try:
                    validated_val = int(val)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Column '{col}' requires an integer value")
            elif "REAL" in col_type or "FLOAT" in col_type or "DOUBLE" in col_type:
                try:
                    validated_val = float(val)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Column '{col}' requires a numeric value")
                    
        cursor.execute(f"UPDATE '{table}' SET {col} = ? WHERE {pk_col} = ?", (validated_val, pk_val))
        conn.commit()
        return {"status": "success"}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/db-viewer/api/fk-link")
def db_viewer_fk_link(
    db: str = Form(...),
    table: str = Form(...),
    pk_col: str = Form(...),
    pk_val: str = Form(...),
    col: str = Form(...),
    target_val: str = Form(None),
    action: str = Form(...)
):
    db_path = get_safe_db_path(db)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        val = None if action == "delete" else target_val
        cursor.execute(f"UPDATE '{table}' SET {col} = ? WHERE {pk_col} = ?", (val, pk_val))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/db-viewer/api/delete")
def db_viewer_delete(
    db: str = Form(...),
    table: str = Form(...),
    pk_col: str = Form(...),
    pk_val: str = Form(...)
):
    db_path = get_safe_db_path(db)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM '{table}' WHERE {pk_col} = ?", (pk_val,))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/db-viewer/api/add")
def db_viewer_add(
    db: str = Form(...),
    table: str = Form(...),
    row_data: str = Form(...)
):
    db_path = get_safe_db_path(db)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        data = json.loads(row_data)
        columns = list(data.keys())
        values = list(data.values())
        
        col_placeholders = ", ".join([f"'{c}'" for c in columns])
        val_placeholders = ", ".join(["?" for _ in values])
        
        cursor.execute(f"INSERT INTO '{table}' ({col_placeholders}) VALUES ({val_placeholders})", values)
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/db-viewer", response_class=HTMLResponse)
def db_viewer():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Database Live Viewer</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(22, 28, 45, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --accent-purple: #8b5cf6;
            --accent-blue: #3b82f6;
            --accent-emerald: #10b981;
            --accent-rose: #f43f5e;
            --glass-shine: rgba(255, 255, 255, 0.03);
        }
        
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        
        body {
            background-color: var(--bg-color);
            color: var(--text-primary);
            font-family: 'Outfit', sans-serif;
            padding: 40px 20px;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(139, 92, 246, 0.05) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(59, 130, 246, 0.05) 0%, transparent 40%);
            background-attachment: fixed;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        header {
            margin-bottom: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 20px;
        }
        
        h1 {
            font-size: 2.2rem;
            font-weight: 700;
            background: linear-gradient(135deg, #a78bfa 0%, #60a5fa 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .section-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(12px);
            margin-bottom: 30px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
        }
        
        select, input, button {
            font-family: inherit;
            font-size: 1rem;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            padding: 10px 14px;
            background: #111827;
            color: white;
            outline: none;
        }
        
        select:focus, input:focus {
            border-color: var(--accent-purple);
        }
        
        button {
            cursor: pointer;
            font-weight: 600;
            background: var(--accent-purple);
            color: white;
            border: none;
            transition: opacity 0.2s;
        }
        
        button:hover {
            opacity: 0.9;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            text-align: left;
        }
        
        th, td {
            padding: 14px;
            border-bottom: 1px solid var(--border-color);
        }
        
        th {
            color: var(--text-secondary);
            text-transform: uppercase;
            font-size: 0.8rem;
            font-weight: 600;
            letter-spacing: 0.05em;
        }
        
        td {
            color: #d1d5db;
        }
        
        .fk-embedded {
            background: rgba(59, 130, 246, 0.08);
            border: 1px solid rgba(59, 130, 246, 0.2);
            border-radius: 6px;
            padding: 10px;
            cursor: pointer;
            font-size: 0.8rem;
            display: inline-block;
            max-width: 250px;
            transition: all 0.2s;
        }
        
        .fk-embedded:hover {
            border-color: var(--accent-blue);
            background: rgba(59, 130, 246, 0.15);
        }
        
        .fk-embedded table {
            margin-top: 6px;
            font-size: 0.75rem;
        }
        
        .fk-embedded th {
            font-size: 0.7rem;
            padding: 2px 4px;
            color: var(--text-secondary);
        }
        
        .fk-embedded td {
            padding: 2px 4px;
            color: white;
        }
        
        .fk-title {
            font-weight: 700;
            color: var(--accent-blue);
            text-transform: uppercase;
            font-size: 0.75rem;
            display: flex;
            justify-content: space-between;
        }
        
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.7);
            align-items: center;
            justify-content: center;
            z-index: 1000;
            backdrop-filter: blur(4px);
        }
        
        .modal-content {
            background: #161c2d;
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 30px;
            width: 90%;
            max-width: 500px;
            max-height: 80vh;
            overflow-y: auto;
            box-shadow: 0 10px 40px rgba(0,0,0,0.5);
        }
        
        .editable {
            cursor: pointer;
            border-bottom: 1px dashed rgba(255,255,255,0.2);
        }
        
        .editable:hover {
            background: rgba(255,255,255,0.03);
        }
        
        .no-records {
            text-align: center;
            color: var(--text-secondary);
            padding: 40px;
            font-style: italic;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>Live Table Viewer</h1>
                <p style="color: var(--text-secondary); margin-top: 4px;"><a href="/" style="color: var(--accent-purple); text-decoration: none; font-weight:600;">← Back to Control Panel</a></p>
            </div>
        </header>

        <div class="section-card">
            <div style="display: flex; gap: 20px; flex-wrap: wrap; align-items: center;">
                <div style="display:flex; flex-direction:column; gap:6px;">
                    <label style="font-size:0.85rem; color:var(--text-secondary);">Select Database:</label>
                    <select id="db-select" onchange="loadTables()"></select>
                </div>
                <div style="display:flex; flex-direction:column; gap:6px;">
                    <label style="font-size:0.85rem; color:var(--text-secondary);">Select Table:</label>
                    <select id="table-select" onchange="loadTableData()"></select>
                </div>
                <div style="margin-top:20px;">
                    <button onclick="showAddRowForm()">+ Add Row</button>
                </div>
            </div>
        </div>

        <div class="section-card" id="table-container" style="overflow-x: auto;">
            <p class="no-records">Select a database and table to load records.</p>
        </div>
    </div>

    <!-- FK Link Modal -->
    <div class="modal" id="fk-modal">
        <div class="modal-content">
            <h3 style="margin-bottom: 20px; color: var(--accent-blue);">Manage Foreign Key Link</h3>
            <p style="color: var(--text-secondary); margin-bottom: 15px;">Link this cell to a record in referenced table:</p>
            <input type="hidden" id="modal-fk-col" />
            <input type="hidden" id="modal-pk-val" />
            <select id="fk-target-select" style="width: 100%; margin-bottom: 20px;"></select>
            <div style="display: flex; gap: 10px; justify-content: flex-end;">
                <button onclick="closeModal()" style="background:#374151;">Cancel</button>
                <button onclick="saveFkLink()" style="background: var(--accent-emerald);">Change/Add Link</button>
                <button onclick="deleteFkLink()" style="background: var(--accent-rose);">Delete Link</button>
            </div>
        </div>
    </div>

    <!-- Add Row Modal -->
    <div class="modal" id="add-row-modal">
        <div class="modal-content">
            <h3 style="margin-bottom: 20px; color: var(--accent-purple);">Add New Row</h3>
            <form id="add-row-form" style="display: flex; flex-direction: column; gap: 15px;" onsubmit="submitNewRow(event)">
                <div id="add-row-inputs" style="display:flex; flex-direction:column; gap:12px;"></div>
                <div style="display: flex; gap: 10px; justify-content: flex-end; margin-top:15px;">
                    <button type="button" onclick="closeAddRowModal()" style="background:#374151;">Cancel</button>
                    <button type="submit" style="background: var(--accent-purple);">Insert Row</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        let currentColumns = [];
        let currentPk = '';
        let currentChoices = {};

        function fetchDatabases() {
            fetch('/db-viewer/api/databases')
                .then(r => r.json())
                .then(dbs => {
                    const select = document.getElementById('db-select');
                    select.innerHTML = '';
                    dbs.forEach(db => {
                        const opt = document.createElement('option');
                        opt.value = db;
                        opt.innerText = db;
                        select.appendChild(opt);
                    });
                    if (dbs.length > 0) loadTables();
                });
        }

        function loadTables() {
            const db = document.getElementById('db-select').value;
            fetch(`/db-viewer/api/tables?db=${db}`)
                .then(r => r.json())
                .then(tables => {
                    const select = document.getElementById('table-select');
                    select.innerHTML = '';
                    tables.forEach(t => {
                        const opt = document.createElement('option');
                        opt.value = t;
                        opt.innerText = t;
                        select.appendChild(opt);
                    });
                    if (tables.length > 0) loadTableData();
                });
        }

        function loadTableData() {
            const db = document.getElementById('db-select').value;
            const table = document.getElementById('table-select').value;
            if (!db || !table) return;

            fetch(`/db-viewer/api/data?db=${db}&table=${table}`)
                .then(r => r.json())
                .then(res => {
                    currentColumns = res.columns;
                    currentPk = res.pk_col;
                    currentChoices = res.fk_choices;

                    const container = document.getElementById('table-container');
                    if (res.rows.length === 0) {
                        container.innerHTML = `<p class="no-records">No rows found in '${table}'.</p>`;
                        return;
                    }

                    let html = '<table><thead><tr>';
                    res.columns.forEach(col => {
                        html += `<th>${col.name}<br/><span style="font-size:0.7rem; color:var(--text-secondary); text-transform:none;">${col.type}</span></th>`;
                    });
                    html += '<th>Actions</th></tr></thead><tbody>';

                    res.rows.forEach(row => {
                        const pkVal = row[currentPk]?.value;
                        html += '<tr>';
                        res.columns.forEach(col => {
                            const cell = row[col.name];
                            if (cell.is_fk) {
                                const resolved = cell.resolved;
                                const refTable = cell.ref_table;
                                const refTo = cell.ref_to;
                                let fkHtml = '';
                                if (resolved) {
                                    fkHtml = `<div class="fk-embedded" onclick="openFkLink('${col.name}', '${pkVal}', '${refTable}')">`;
                                    fkHtml += `<div class="fk-title"><span>${col.name}</span> <span style="font-size:0.65rem; color:var(--accent-blue); opacity:0.7;">➔ ${refTable}</span></div>`;
                                    fkHtml += '<table><thead><tr>';
                                    Object.keys(resolved).forEach(k => {
                                        fkHtml += `<th>${k}</th>`;
                                    });
                                    fkHtml += '</tr></thead><tbody><tr>';
                                    Object.values(resolved).forEach(v => {
                                        fkHtml += `<td>${v !== null ? v : ''}</td>`;
                                    });
                                    fkHtml += '</tr></tbody></table></div>';
                                } else {
                                    fkHtml = `<button onclick="openFkLink('${col.name}', '${pkVal}', '${refTable}')" style="font-size:0.75rem; padding: 4px 8px; background:var(--accent-blue);">Change/Add Link</button>`;
                                }
                                html += `<td>${fkHtml}</td>`;
                            } else {
                                if (col.pk) {
                                    html += `<td><strong>${cell.value}</strong></td>`;
                                } else {
                                    html += `<td class="editable" data-col="${col.name}" data-pk="${pkVal}" data-type="${col.type}" onclick="startEdit(this)">${cell.value !== null ? cell.value : ''}</td>`;
                                }
                            }
                        });
                        html += `<td><button style="background:var(--accent-rose); font-size:0.8rem; padding: 6px 10px;" onclick="deleteRow('${pkVal}')">Delete</button></td>`;
                        html += '</tr>';
                    });

                    html += '</tbody></table>';
                    container.innerHTML = html;
                });
        }

        function startEdit(cell) {
            if (cell.querySelector('input')) return;
            const originalVal = cell.innerText;
            const type = cell.getAttribute('data-type');
            
            const input = document.createElement('input');
            input.type = 'text';
            input.value = originalVal;
            input.style.width = '100%';
            input.style.padding = '4px 8px';
            input.style.fontSize = 'inherit';
            input.style.background = '#1f2937';
            input.style.color = 'white';
            
            cell.innerHTML = '';
            cell.appendChild(input);
            input.focus();

            function commitChange() {
                const newVal = input.value;
                if (newVal === originalVal) {
                    cell.innerHTML = originalVal;
                    return;
                }
                
                // Strict validation before API call
                if (type.includes('INT')) {
                    if (newVal !== '' && isNaN(parseInt(newVal))) {
                        alert("Validation error: Value must be an integer.");
                        cell.innerHTML = originalVal;
                        return;
                    }
                } else if (type.includes('REAL') || type.includes('FLOAT') || type.includes('DOUBLE')) {
                    if (newVal !== '' && isNaN(parseFloat(newVal))) {
                        alert("Validation error: Value must be a float.");
                        cell.innerHTML = originalVal;
                        return;
                    }
                }

                const db = document.getElementById('db-select').value;
                const table = document.getElementById('table-select').value;
                const col = cell.getAttribute('data-col');
                const pkVal = cell.getAttribute('data-pk');

                const fd = new FormData();
                fd.append('db', db);
                fd.append('table', table);
                fd.append('pk_col', currentPk);
                fd.append('pk_val', pkVal);
                fd.append('col', col);
                fd.append('val', newVal);

                fetch('/db-viewer/api/edit', { method: 'POST', body: fd })
                    .then(r => {
                        if (!r.ok) return r.json().then(e => { throw new Error(e.detail) });
                        return r.json();
                    })
                    .then(() => {
                        cell.innerHTML = newVal;
                    })
                    .catch(err => {
                        alert("Save failed: " + err.message);
                        cell.innerHTML = originalVal;
                    });
            }

            input.onblur = commitChange;
            input.onkeydown = function(e) {
                if (e.key === 'Enter') commitChange();
                if (e.key === 'Escape') cell.innerHTML = originalVal;
            };
        }

        function openFkLink(colName, pkVal, refTable) {
            document.getElementById('modal-fk-col').value = colName;
            document.getElementById('modal-pk-val').value = pkVal;
            
            const choices = currentChoices[refTable] || [];
            const select = document.getElementById('fk-target-select');
            select.innerHTML = '<option value="">-- Select target record --</option>';
            choices.forEach(ch => {
                const opt = document.createElement('option');
                opt.value = ch.id || Object.values(ch)[0];
                opt.innerText = JSON.stringify(ch);
                select.appendChild(opt);
            });
            
            document.getElementById('fk-modal').style.display = 'flex';
        }

        function closeModal() {
            document.getElementById('fk-modal').style.display = 'none';
        }

        function saveFkLink() {
            const db = document.getElementById('db-select').value;
            const table = document.getElementById('table-select').value;
            const col = document.getElementById('modal-fk-col').value;
            const pkVal = document.getElementById('modal-pk-val').value;
            const targetVal = document.getElementById('fk-target-select').value;

            const fd = new FormData();
            fd.append('db', db);
            fd.append('table', table);
            fd.append('pk_col', currentPk);
            fd.append('pk_val', pkVal);
            fd.append('col', col);
            fd.append('target_val', targetVal);
            fd.append('action', 'change');

            fetch('/db-viewer/api/fk-link', { method: 'POST', body: fd })
                .then(r => r.json())
                .then(() => {
                    closeModal();
                    loadTableData();
                });
        }

        function deleteFkLink() {
            const db = document.getElementById('db-select').value;
            const table = document.getElementById('table-select').value;
            const col = document.getElementById('modal-fk-col').value;
            const pkVal = document.getElementById('modal-pk-val').value;

            const fd = new FormData();
            fd.append('db', db);
            fd.append('table', table);
            fd.append('pk_col', currentPk);
            fd.append('pk_val', pkVal);
            fd.append('col', col);
            fd.append('action', 'delete');

            fetch('/db-viewer/api/fk-link', { method: 'POST', body: fd })
                .then(r => r.json())
                .then(() => {
                    closeModal();
                    loadTableData();
                });
        }

        function deleteRow(pkVal) {
            if (!confirm("Are you sure you want to delete this row?")) return;
            const db = document.getElementById('db-select').value;
            const table = document.getElementById('table-select').value;

            const fd = new FormData();
            fd.append('db', db);
            fd.append('table', table);
            fd.append('pk_col', currentPk);
            fd.append('pk_val', pkVal);

            fetch('/db-viewer/api/delete', { method: 'POST', body: fd })
                .then(r => r.json())
                .then(() => {
                    loadTableData();
                });
        }

        function showAddRowForm() {
            const inputsContainer = document.getElementById('add-row-inputs');
            inputsContainer.innerHTML = '';
            
            currentColumns.forEach(col => {
                if (col.pk && col.type.includes('INT')) return;
                
                const fieldDiv = document.createElement('div');
                fieldDiv.style.display = 'flex';
                fieldDiv.style.flexDirection = 'column';
                fieldDiv.style.gap = '4px';
                
                const label = document.createElement('label');
                label.innerText = `${col.name} (${col.type}):`;
                label.style.fontSize = '0.85rem';
                label.style.color = 'var(--text-secondary)';
                fieldDiv.appendChild(label);
                
                const input = document.createElement('input');
                input.type = 'text';
                input.name = col.name;
                input.setAttribute('data-type', col.type);
                fieldDiv.appendChild(input);
                
                inputsContainer.appendChild(fieldDiv);
            });
            
            document.getElementById('add-row-modal').style.display = 'flex';
        }

        function closeAddRowModal() {
            document.getElementById('add-row-modal').style.display = 'none';
        }

        function submitNewRow(e) {
            e.preventDefault();
            const db = document.getElementById('db-select').value;
            const table = document.getElementById('table-select').value;
            
            const inputs = document.getElementById('add-row-inputs').querySelectorAll('input');
            const data = {};
            
            for (let i = 0; i < inputs.length; i++) {
                const inp = inputs[i];
                const type = inp.getAttribute('data-type');
                const val = inp.value.trim();
                
                if (val !== '') {
                    if (type.includes('INT')) {
                        if (isNaN(parseInt(val))) {
                            alert(`Validation error: Column '${inp.name}' must be an integer.`);
                            return;
                        }
                        data[inp.name] = parseInt(val);
                    } else if (type.includes('REAL') || type.includes('FLOAT') || type.includes('DOUBLE')) {
                        if (isNaN(parseFloat(val))) {
                            alert(`Validation error: Column '${inp.name}' must be a float.`);
                            return;
                        }
                        data[inp.name] = parseFloat(val);
                    } else {
                        data[inp.name] = val;
                    }
                }
            }

            const fd = new FormData();
            fd.append('db', db);
            fd.append('table', table);
            fd.append('row_data', JSON.stringify(data));

            fetch('/db-viewer/api/add', { method: 'POST', body: fd })
                .then(r => r.json())
                .then(() => {
                    closeAddRowModal();
                    loadTableData();
                });
        }

        fetchDatabases();
    </script>
</body>
</html>"""
    return html

@app.get("/setup", response_class=HTMLResponse)
def setup_wizard():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT site_name, url FROM budget_sites")
    sites = cursor.fetchall()
    cursor.execute("SELECT email_address FROM email_accounts")
    emails = cursor.fetchall()
    conn.close()
    
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agentic Accountant Setup Wizard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(17, 24, 39, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --accent-purple: #8b5cf6;
            --accent-emerald: #10b981;
            --accent-rose: #f43f5e;
            --accent-blue: #3b82f6;
            --glass-shine: rgba(255, 255, 255, 0.03);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 40px 20px;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(139, 92, 246, 0.05) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(59, 130, 246, 0.05) 0%, transparent 40%);
            background-attachment: fixed;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        
        header {
            margin-bottom: 40px;
            text-align: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 20px;
        }
        
        h1 {
            font-size: 2.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, #a78bfa 0%, #60a5fa 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: inline-block;
        }
        
        .setup-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 30px;
            margin-bottom: 30px;
            backdrop-filter: blur(12px);
        }
        
        h2 {
            font-size: 1.5rem;
            margin-bottom: 20px;
            color: var(--accent-purple);
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            color: var(--text-secondary);
            font-size: 0.95rem;
            font-weight: 600;
        }
        
        input[type="text"], input[type="email"] {
            width: 100%;
            padding: 12px 16px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: rgba(31, 41, 55, 0.5);
            color: white;
            font-size: 1rem;
            outline: none;
            transition: border-color 0.2s;
        }
        
        input:focus {
            border-color: var(--accent-purple);
        }
        
        button {
            background: var(--accent-purple);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        button:hover {
            opacity: 0.9;
            transform: translateY(-1px);
        }
        
        .list-items {
            margin-top: 20px;
            border-top: 1px solid var(--border-color);
            padding-top: 20px;
        }
        
        .list-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px;
            background: rgba(255, 255, 255, 0.02);
            border-radius: 8px;
            margin-bottom: 10px;
            border: 1px solid var(--border-color);
        }
        
        .list-item-name {
            font-weight: 600;
        }
        
        .list-item-sub {
            color: var(--text-secondary);
            font-size: 0.9rem;
            font-family: 'JetBrains Mono', monospace;
        }
        
        .btn-delete {
            color: var(--accent-rose);
            text-decoration: none;
            font-weight: 600;
            font-size: 0.9rem;
        }
        
        .alert-info {
            background: rgba(59, 130, 246, 0.1);
            border: 1px solid rgba(59, 130, 246, 0.2);
            color: #93c5fd;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 0.95rem;
            line-height: 1.5;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Setup Wizard</h1>
            <p style="color: var(--text-secondary); margin-top: 8px;">Configure your budget portal and secure email sessions</p>
        </header>
        
        <!-- Step 1: Budget Portal -->
        <div class="setup-card">
            <h2>Step 1: Configure Budget Portal</h2>
            <form action="/setup/budget" method="post">
                <div class="form-group">
                    <label>Software Name</label>
                    <input type="text" name="site_name" placeholder="e.g. Monarch Money, YNAB, Quicken Simplifi" required />
                </div>
                <div class="form-group">
                    <label>Portal Login URL</label>
                    <input type="text" name="url" placeholder="https://..." required />
                </div>
                <button type="submit">Save Budget Portal</button>
            </form>
            
            <div class="list-items">
                <strong>Configured Portals:</strong>
                """
    if not sites:
        html += "<p class='no-records' style='margin-top: 10px;'>No portals configured yet.</p>"
    else:
        for s_name, s_url in sites:
            html += f"""
                <div class="list-item">
                    <div>
                        <div class="list-item-name">{s_name}</div>
                        <div class="list-item-sub">{s_url}</div>
                    </div>
                    <a href="/setup/delete-budget?site_name={s_name}" class="btn-delete">Delete</a>
                </div>
            """
            
    html += """
            </div>
        </div>
        
        <!-- Step 2: Email Accounts -->
        <div class="setup-card">
            <h2>Step 2: Add Email Accounts (No App Passwords Required)</h2>
            <form action="/setup/email" method="post">
                <div class="form-group">
                    <label>Email Address</label>
                    <input type="email" name="email_address" placeholder="e.g. user@gmail.com" required />
                </div>
                <button type="submit">Add Email Account</button>
            </form>
            
            <div class="list-items">
                <strong>Registered Emails:</strong>
                """
    if not emails:
        html += "<p class='no-records' style='margin-top: 10px;'>No email accounts registered yet.</p>"
    else:
        for (e_addr,) in emails:
            html += f"""
                <div class="list-item">
                    <span class="list-item-name">{e_addr}</span>
                    <a href="/setup/delete-email?email_address={e_addr}" class="btn-delete">Delete</a>
                </div>
            """
            
    html += """
            </div>
        </div>
        
        <!-- Step 3: Interactive Login -->
        <div class="setup-card" style="border-color: var(--accent-emerald);">
            <h2 style="color: var(--accent-emerald);">Step 3: Establish Secure Session Cookies</h2>
            <div class="alert-info">
                The Setup Wizard will launch Chromium in headed mode on your machine. 
                Log in to each budget portal and email service tab manually. Playwright will store the session cookies locally. 
                Once logged in, close the browser window to complete setup. Plaintext passwords are never saved.
            </div>
            <form action="/setup/launch-auth" method="post">
                <button type="submit" style="background: var(--accent-emerald); width: 100%; padding: 15px; font-size: 1.1rem;">Launch Secure Authentication Browser</button>
            </form>
            
            <div style="margin-top: 20px; text-align: center;">
                <a href="/" style="color: var(--text-secondary); text-decoration: none; font-weight: 600;">Go to Dashboard →</a>
            </div>
        </div>
    </div>
</body>
</html>"""
    return html

@app.post("/setup/budget")
def setup_budget(site_name: str = Form(...), url: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        # Normalize target key name
        normalized_key = site_name.lower().replace(" ", "_") + "_sync"
        cursor.execute("INSERT INTO budget_sites (site_name, url) VALUES (?, ?)", (normalized_key, url))
        conn.commit()
        
        # Read modules_config.json and insert config profile if not exists
        import json
        config_path = "modules_config.json"
        if os.path.exists("/workspace/modules_config.json"):
            config_path = "/workspace/modules_config.json"
            
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            
            if normalized_key not in cfg:
                cfg[normalized_key] = {
                    "steps": [
                        {"action": "goto", "url": url},
                        {"action": "wait_for_selector", "selector": "table, .transactions, .ledger"},
                        {"action": "scrape_transactions", "row_selector": "tr, .transaction-row", "columns": {"date": "td.date", "payee": "td.payee", "amount": "td.amount", "category": "td.category", "account": "td.account"}},
                        {"action": "imap_sync"},
                        {"action": "run_reconciliation_logic"}
                    ]
                }
                if "execution_order" in cfg:
                    if normalized_key not in cfg["execution_order"]:
                        cfg["execution_order"].append(normalized_key)
                else:
                    cfg["execution_order"] = [normalized_key]
                    
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Error saving budget portal: {e}")
    finally:
        conn.close()
    return RedirectResponse(url="/setup", status_code=303)

@app.post("/setup/email")
def setup_email(email_address: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT INTO email_accounts (email_address) VALUES (?)", (email_address,))
        conn.commit()
    except Exception as e:
        print(f"Error saving email account: {e}")
    finally:
        conn.close()
    return RedirectResponse(url="/setup", status_code=303)

@app.get("/setup/delete-budget")
def delete_budget(site_name: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM budget_sites WHERE site_name = ?", (site_name,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/setup", status_code=303)

@app.get("/setup/delete-email")
def delete_email(email_address: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM email_accounts WHERE email_address = ?", (email_address,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/setup", status_code=303)

@app.post("/setup/launch-auth")
def launch_auth(request: Request):
    from playwright.sync_api import sync_playwright
    import threading
    
    def run_auth_browser():
        with sync_playwright() as p:
            is_headless = False
            browser = p.chromium.launch_persistent_context(
                user_data_dir="/workspace/user_session_data" if os.path.exists("/workspace") else "user_session_data",
                headless=is_headless,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1440, "height": 900},
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT url FROM budget_sites")
            site_urls = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT email_address FROM email_accounts")
            emails = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            for url in site_urls:
                page = browser.new_page()
                page.goto(url)
                
            for email_addr in emails:
                domain = email_addr.split("@")[-1].lower()
                if "gmail" in domain:
                    mail_url = "https://mail.google.com/mail/"
                elif "outlook" in domain or "hotmail" in domain or "live" in domain:
                    mail_url = "https://outlook.live.com/mail/"
                else:
                    mail_url = f"https://mail.{domain}"
                page = browser.new_page()
                page.goto(mail_url)
                
            try:
                while len(browser.pages) > 0:
                    time.sleep(1)
            except Exception:
                pass
            browser.close()
            
    threading.Thread(target=run_auth_browser, daemon=True).start()
    return RedirectResponse(url="/setup", status_code=303)

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

# Start background command polling loop
threading.Thread(target=command_worker_loop, daemon=True).start()

@app.get("/", response_class=HTMLResponse)
def dashboard():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Fetch latest report
    cursor.execute("SELECT report_content, timestamp FROM daily_reports ORDER BY id DESC LIMIT 1")
    latest_report = cursor.fetchone()
    
    # Fetch review queue items
    cursor.execute("SELECT transaction_id, date, payee, amount, loop_state, proposed_rule FROM review_queue WHERE loop_state != 'APPROVED'")
    items = cursor.fetchall()
    
    # Fetch permanent rules
    cursor.execute("SELECT id, keyword_pattern, target_category, target_bill_name FROM permanent_mapping_rules")
    rules = cursor.fetchall()
    
    # Stats
    cursor.execute("SELECT COUNT(*) FROM raw_transactions")
    total_tx = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM review_queue WHERE loop_state = 'APPROVED'")
    reconciled_tx = cursor.fetchone()[0]
    pending_tx = len(items)
    
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
    
    if not items:
        html += "<p class='no-records' style='color: var(--accent-emerald); font-weight: 600;'>✓ All transactions mapped and reconciled.</p>"
    else:
        for tx_id, dt, py, am, state, prop_rule in items:
            state_label = "Rule Proposed" if state == "RULE_PROPOSED" else ("Rejected" if state == "REJECTED" else "Pending Input")
            state_class = "proposed" if state == "RULE_PROPOSED" else ("rejected" if state == "REJECTED" else "")
            
            html += "<div class='queue-item'>"
            html += "  <div class='queue-header'>"
            html += "    <div class='queue-meta'>"
            html += "      <span class='tx-payee'>" + str(py) + "</span>"
            html += "      <span class='tx-amount'>$" + f"{am:.2f}" + "</span>"
            html += "      <span class='tx-date'>" + str(dt) + "</span>"
            html += "    </div>"
            html += "    <span class='badge-state " + state_class + "'>" + state_label + "</span>"
            html += "  </div>"
            
            if state in ("PENDING_INPUT", "REJECTED"):
                placeholder_text = "Provide context to re-draft this rule..." if state == "REJECTED" else "Provide context for this transaction (e.g., business lunch, office supplies)..."
                html += "  <form action='/submit-input' method='post'>"
                html += "    <input type='hidden' name='tx_id' value='" + str(tx_id) + "'/>"
                html += "    <input type='text' name='user_text' placeholder='" + placeholder_text + "' required/>"
                html += "    <button type='submit'>Submit Context</button>"
                html += "  </form>"
            elif state == "RULE_PROPOSED":
                html += "  <div class='proposed-rule-box'>" + str(prop_rule) + "</div>"
                html += "  <div class='action-buttons'>"
                html += "    <a href='/action-rule?tx_id=" + str(tx_id) + "&action=approve' class='btn-action approve'>Approve & Writeback</a>"
                html += "    <a href='/action-rule?tx_id=" + str(tx_id) + "&action=reject' class='btn-action reject'>Reject & Redraft</a>"
                html += "  </div>"
            html += "</div>"

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

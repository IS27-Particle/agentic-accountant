import sqlite3
import json
import datetime
import re
import os
import database

def get_eligible_transactions(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT id, date, payee, amount, category, account, writeback_status, cleared FROM raw_transactions")
    all_txs = cursor.fetchall()
    eligible = []
    for tx in all_txs:
        tx_id, dt, payee, amount, category, account, wb_status, cleared = tx
        # Rule 1.1: Only run validations for transactions which have cleared inside the transaction database.
        # Exception: Non-Connected Account transactions (example: One Main/OneMain)
        is_non_connected = False
        if account and ("one main" in account.lower() or "onemain" in account.lower()):
            is_non_connected = True
            
        if cleared == 'CLEARED' or is_non_connected:
            eligible.append({
                'id': tx_id,
                'date': dt,
                'payee': payee,
                'amount': amount,
                'category': category,
                'account': account,
                'writeback_status': wb_status,
                'cleared': cleared
            })
    return eligible

def classify_transaction(tx, conn):
    classifications = []
    category = tx.get('category') or ''
    payee = tx.get('payee') or ''
    amount = tx.get('amount', 0)
    
    # 1. Transfer Check
    is_transfer = False
    if category.startswith("Transfer/") or "Transfer" in category or "transfer" in payee.lower():
        is_transfer = True
        classifications.append("Transfer")
        
    # 2. Bill/Subscription Check
    is_bill = False
    cursor = conn.cursor()
    cursor.execute("SELECT target_bill_name FROM permanent_mapping_rules WHERE ? LIKE '%' || keyword_pattern || '%'", (payee,))
    rule = cursor.fetchone()
    if rule and rule[0]:
        is_bill = True
        classifications.append("Bill/Subscription")
    elif any(kw in payee.lower() for kw in ["netflix", "spotify", "hulu", "comcast", "xfinity", "insurance", "electric", "water", "bill", "subscription"]):
        is_bill = True
        classifications.append("Bill/Subscription")
        
    # 3. Income Check
    if amount > 0 and not is_transfer:
        classifications.append("Income")
        
    # 4. Expense Check
    if amount <= 0 and not is_transfer and not is_bill:
        classifications.append("Expense")
        
    if not classifications:
        classifications.append("Expense")
        
    return classifications

def queue_task(conn, category, transaction_id, details, explanation, accept_changes, reject_changes):
    cursor = conn.cursor()
    # Check if duplicate pending task exists
    cursor.execute("SELECT id FROM task_queue WHERE category = ? AND transaction_id = ? AND status = 'PENDING'", (category, transaction_id))
    exists = cursor.fetchone()
    if exists:
        return exists[0]
        
    cursor.execute("""
        INSERT INTO task_queue (category, transaction_id, details, explanation, accept_changes, reject_changes, status)
        VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
    """, (category, transaction_id, json.dumps(details), explanation, accept_changes, reject_changes))
    conn.commit()
    task_id = cursor.lastrowid
    
    # Send webhook / log audit
    database.log_audit(conn, "VALIDATOR", f"QUEUED_TASK_{category.replace(' ', '_').upper()}", f"Task ID {task_id} queued for transaction {transaction_id}")
    return task_id

def remove_pending_task(conn, category, transaction_id):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM task_queue WHERE category = ? AND transaction_id = ? AND status = 'PENDING'", (category, transaction_id))
    conn.commit()

# --- Evaluation Handlers ---

def handle_bill_subscription(tx, conn):
    cursor = conn.cursor()
    payee = tx['payee']
    amount = tx['amount']
    tx_id = tx['id']
    
    # Check if matching Bill/Subscription exists in local databases (mapping rules)
    cursor.execute("SELECT id, keyword_pattern, target_bill_name, target_category FROM permanent_mapping_rules WHERE ? LIKE '%' || keyword_pattern || '%'", (payee,))
    rule = cursor.fetchone()
    
    if rule:
        rule_id, pattern, bill_name, target_cat = rule
        if not bill_name:
            # Matches category rule but not specifically tagged to a bill series
            explanation = f"Matching recurring rule found ({pattern}) but it is not linked to a bill series inside Simplifi."
            details = {
                "transaction": tx,
                "proposed_bill": pattern,
                "current_category": target_cat
            }
            accept = f"Attach transaction to Series: {pattern}. Category to: {target_cat}"
            reject = "Keep as standard expense without bill linkage."
            queue_task(conn, "Bill Attachment", tx_id, details, explanation, accept, reject)
        else:
            # Confirm is attached (or proposed to be attached)
            has_fluctuated = False
            cursor.execute("SELECT amount, date, id FROM raw_transactions WHERE payee LIKE '%' || ? || '%' AND id != ? ORDER BY date DESC LIMIT 5", (pattern, tx_id))
            recent_txs = cursor.fetchall()
            
            if recent_txs:
                avg_amount = sum(r[0] for r in recent_txs) / len(recent_txs)
                if avg_amount != 0 and abs(amount - avg_amount) / abs(avg_amount) > 0.05:
                    has_fluctuated = True
            
            if has_fluctuated:
                explanation = f"Detected bill amount fluctuation for '{bill_name}'. Current amount ${amount:.2f} varies from recent average ${avg_amount:.2f}."
                details = {
                    "transaction": tx,
                    "series": bill_name,
                    "recent_5_transactions": [{"id": r[2], "amount": r[0], "date": r[1]} for r in recent_txs],
                    "marker_transaction_id": tx_id
                }
                accept = f"Adjust Simplifi recurring bill series '{bill_name}' expectations to match new amount."
                reject = "Ignore amount change; keep original bill expectation."
                queue_task(conn, "Bill Adjustment", tx_id, details, explanation, accept, reject)
            elif tx['writeback_status'] == 'PENDING':
                explanation = f"Confirm attachment of transaction to the active '{bill_name}' series. Historical data shows {len(recent_txs)} similar payments."
                details = {
                    "transaction": tx,
                    "bill_name": bill_name,
                    "history": [{"amount": h[0], "date": h[1]} for h in recent_txs]
                }
                accept = f"Confirm bill attachment of {payee} (${amount}) to Simplifi series '{bill_name}'."
                reject = "Skip bill attachment for this month's billing cycle."
                queue_task(conn, "Bill Attachment", tx_id, details, explanation, accept, reject)
    else:
        # Bill Addition path (4.2.2)
        # Search previous transactions to see if this is recurring
        cursor.execute("SELECT amount, date FROM raw_transactions WHERE payee = ? AND id != ?", (payee, tx_id))
        history = cursor.fetchall()
        
        explanation = f"New potential recurring payment detected for '{payee}'. History contains {len(history)} matching entries."
        details = {
            "transaction": tx,
            "history": [{"amount": h[0], "date": h[1]} for h in history],
            "proposed_series": f"Series: {payee} | Category: Bills & Utilities"
        }
        accept = f"Create a new Bill Series in Simplifi for '{payee}'."
        reject = "Leave as one-off expense."
        queue_task(conn, "Bill Addition", tx_id, details, explanation, accept, reject)

def handle_transfer(tx, conn):
    cursor = conn.cursor()
    category = tx['category'] or ''
    amount = tx['amount']
    date_str = tx['date']
    tx_id = tx['id']
    
    # 4.3.1. Verify destination account exists in budget_sites or simplifi_connections
    match = re.match(r"Transfer/(.+)", category)
    target_account = match.group(1).strip() if match else None
    
    if not target_account:
        explanation = "Categorized as a transfer, but destination account is unspecified."
        details = {"transaction": tx}
        accept = "Queue to specify destination account profile."
        reject = "Re-classify as non-transfer expense."
        queue_task(conn, "Transfer Missing Category", tx_id, details, explanation, accept, reject)
        return
        
    cursor.execute("SELECT account_name FROM simplifi_connections WHERE account_name = ?", (target_account,))
    account_exists = cursor.fetchone()
    
    if not account_exists:
        explanation = f"Transfer category targets account '{target_account}', which is not registered in system connection profiles."
        details = {"transaction": tx, "target_account": target_account}
        accept = f"Create configuration profile for '{target_account}'."
        reject = "Redirect transfer target to an existing account category."
        queue_task(conn, "Transfer Missing Category", tx_id, details, explanation, accept, reject)
        return
        
    # Search for related reciprocating transaction inside target account
    # Flexible 1 to 3 day offset (bank clearing delays)
    try:
        source_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        # Fallback if date is not in standard YYYY-MM-DD
        source_date = datetime.datetime.now()
        
    min_date = (source_date - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    max_date = (source_date + datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    
    # Reciprocating transaction should have opposite sign amount
    target_amount = -amount
    
    cursor.execute("""
        SELECT id, date, payee, amount, category, account, cleared 
        FROM raw_transactions 
        WHERE account = ? AND amount = ? AND date >= ? AND date <= ?
    """, (target_account, target_amount, min_date, max_date))
    recip = cursor.fetchone()
    
    if recip:
        recip_id, r_date, r_payee, r_amount, r_category, r_account, r_cleared = recip
        # Found reciprocating transaction
        explanation = f"Reciprocating transfer transaction found in target ledger '{target_account}'."
        details = {
            "source_transaction": tx,
            "target_transaction": {
                "id": recip_id,
                "date": r_date,
                "payee": r_payee,
                "amount": r_amount,
                "category": r_category,
                "account": r_account,
                "cleared": r_cleared
            }
        }
        accept = f"Link source transaction {tx_id} with target transaction {recip_id}."
        reject = "Keep as independent unlinked transactions."
        queue_task(conn, "Transfer Attachment", tx_id, details, explanation, accept, reject)
    else:
        # Check history for patterns
        cursor.execute("SELECT payee, amount, account FROM raw_transactions WHERE payee LIKE '%Transfer%' AND account = ? LIMIT 5", (tx['account'],))
        history = cursor.fetchall()
        
        explanation = f"Could not find matching cleared or uncleared reciprocating transaction on target account '{target_account}' ledger."
        details = {
            "source_transaction": tx,
            "history_patterns": [{"payee": h[0], "amount": h[1], "account": h[2]} for h in history]
        }
        accept = "Redirect to data editor viewer to manually match or enter reciprocating transaction."
        reject = "Leave unmatched."
        queue_task(conn, "Transfer Transaction Match", tx_id, details, explanation, accept, reject)

def handle_expense(tx, conn):
    cursor = conn.cursor()
    payee = tx['payee']
    tx_id = tx['id']
    
    # 4.4.1 Identify merchant payee
    is_unidentified_payee = "unknown" in payee.lower() or not payee.strip()
    
    # 4.4.2 Identify retailer merchant
    # Check if merchant is registered in modules_config.json
    config_path = os.environ.get("CONFIG_PATH", "modules_config.json")
    known_retailers = []
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
                known_retailers = [k.replace("_sync", "") for k in cfg.keys() if k.endswith("_sync")]
        except Exception:
            pass
            
    is_unidentified_retailer = True
    for retailer in known_retailers:
        if retailer.lower() in payee.lower():
            is_unidentified_retailer = False
            break
            
    # Check ignore list before lookup (hardcoded ignore list example)
    ignore_list = ["subscription", "interest", "bank fee", "cleared", "payment"]
    if any(ig in payee.lower() for ig in ignore_list):
        is_unidentified_retailer = False
        
    # Search email archives in family_context
    cursor.execute("SELECT content FROM family_context WHERE key LIKE 'Email_%' AND content LIKE '%' || ? || '%'", (payee,))
    email_found = cursor.fetchone()
    
    # Determine category matching previous transactions
    cursor.execute("SELECT category FROM raw_transactions WHERE payee = ? AND category IS NOT NULL AND category != 'Uncategorized' LIMIT 1", (payee,))
    prev_cat = cursor.fetchone()
    category_match = prev_cat[0] if prev_cat else None
    
    # 4.4.3 Split check - does the receipt or details suggest multiple items/categories?
    # Simple keyword heuristic check (e.g. Walmart, Amazon tend to have split categories)
    needs_split = any(kw in payee.lower() for kw in ["walmart", "amazon", "target", "costco"]) and not category_match
    
    # Queue processing logic (conditional task processing tree 4.4.4)
    tasks_to_queue = []
    
    if is_unidentified_payee:
        tasks_to_queue.append(("New Payee", {
            "transaction": tx
        }, "Transaction payee is blank or unidentified.", "Input payee name.", "Ignore payee designation."))
        
    if is_unidentified_retailer and not is_unidentified_payee:
        tasks_to_queue.append(("Unknown Retailer", {
            "transaction": tx
        }, "Merchant payee is not mapped to any known retailer configuration profile.", "Provide URL to merchant profile login page.", "Skip retail scraping for payee."))
        
    if not category_match:
        tasks_to_queue.append(("New Category", {
            "transaction": tx
        }, "Unable to resolve category mapping rule for merchant.", "Create mapping rule to standard category.", "Map to Uncategorized."))
        
    if needs_split:
        tasks_to_queue.append(("Expense Split", {
            "transaction": tx,
            "suggested_splits": [{"category": "Groceries", "amount": 0}, {"category": "Shopping", "amount": 0}]
        }, "Transaction originated from a multi-category retailer. Split recommended.", "Allocate amounts to multiple category breakdowns.", "Map entirely to single category."))
        
        # If unable to allocate exact amounts
        if "amazon" in payee.lower() and not email_found:
            tasks_to_queue.append(("Expense Split with Flagged Amounts", {
                "transaction": tx,
                "reason": "Missing email receipt context to allocate exact amounts."
            }, "Unable to allocate exact amounts to split divisions due to missing receipt context.", "Provide manual split amounts.", "Re-queue base Expense Split."))

    # 4.4.5 Prioritization wait-queue ordering logic
    # We resolve from highest priority: Expense Split with Flagged Amounts -> Expense Split -> New Category -> New Payee -> Unknown Retailer
    # To implement this, we queue all relevant alerts. If a higher one exists, we remove/supersede the lower one.
    if any(t[0] == "Expense Split with Flagged Amounts" for t in tasks_to_queue):
        # Remove base Expense Split if present
        tasks_to_queue = [t for t in tasks_to_queue if t[0] != "Expense Split"]
        
    if any(t[0] == "Expense Split" for t in tasks_to_queue) or any(t[0] == "Expense Split with Flagged Amounts" for t in tasks_to_queue):
        # Remove New Category if present
        tasks_to_queue = [t for t in tasks_to_queue if t[0] != "New Category"]
        
    # Now queue whatever tasks survived the priority tree
    for cat, details, exp, acc, rej in tasks_to_queue:
        queue_task(conn, cat, tx_id, details, exp, acc, rej)
        
    # If no exceptions were queued, log an Expense Match
    if not tasks_to_queue:
        # Category matches, payee is known - successful auto-match!
        # Just write status or log match
        pass

def handle_income(tx, conn):
    cursor = conn.cursor()
    payee = tx['payee']
    amount = tx['amount']
    category = tx['category'] or ''
    tx_id = tx['id']
    
    # 4.5.1 normal paycheck paycheck split
    if any(kw in payee.lower() for kw in ["payroll", "paycheck", "direct deposit", "employer", "salary"]):
        # Confirm paycheck series exists
        cursor.execute("SELECT target_category, target_bill_name FROM permanent_mapping_rules WHERE keyword_pattern = 'Paycheck' OR keyword_pattern = ?", (payee,))
        rule = cursor.fetchone()
        
        explanation = f"Income transaction matches regular paycheck deposit profile. Check split mappings across accounts."
        details = {
            "transaction": tx,
            "paycheck_series": rule[1] if rule else "Salary Deposit"
        }
        accept = "Verify paycheck split category allocation inside budget app."
        reject = "Leave paycheck uncategorized."
        queue_task(conn, "Income Source", tx_id, details, explanation, accept, reject)
        return
        
    # 4.5.2 Returned Transaction refund matching
    if any(kw in payee.lower() for kw in ["refund", "return", "reimbursement", "credit credit"]):
        explanation = "Returned transaction refund detected. Search matching debit item to resolve balance."
        details = {"transaction": tx}
        accept = "Redirect to Refund Dashboard sequence to confirm connection to original transaction."
        reject = "Leave refund unlinked."
        queue_task(conn, "Returned Transaction", tx_id, details, explanation, accept, reject)
        return
        
    # 5.3 Personal Payment (Venmo, PayPal, Zelle)
    if any(kw in payee.lower() for kw in ["venmo", "paypal", "zelle"]):
        # Validate category Transfer (no account)
        explanation = "Personal payment received via digital portal. Mapping category to 'Transfer (no account)'."
        details = {"transaction": tx}
        accept = "Confirm category mapping is 'Transfer (no account)'."
        reject = "Allow other custom category definition."
        queue_task(conn, "Personal Payment", tx_id, details, explanation, accept, reject)
        return
        
    # 5.4 Bank Payment (Interest)
    if any(kw in payee.lower() for kw in ["interest", "bank yield", "dividend"]):
        explanation = "Bank interest payment received. Confirm Interest category allocation."
        details = {"transaction": tx}
        accept = "Verify category mapping is 'Interest Income'."
        reject = "Change category to investment returns."
        queue_task(conn, "Bank Payment", tx_id, details, explanation, accept, reject)
        return
        
    # 5.5 Other Income
    explanation = f"Income source identified from payee '{payee}'. Confirm categorization rules."
    details = {"transaction": tx}
    accept = f"Establish mapping rule context for '{payee}'."
    reject = "Treat as uncategorized misc income."
    queue_task(conn, "Income Source", tx_id, details, explanation, accept, reject)

# --- Retailer Steps & Automation Checks ---

def run_retailer_check(tx, conn):
    cursor = conn.cursor()
    payee = tx['payee']
    tx_id = tx['id']
    
    # 5.1 & 5.2 Confirm if retailer config present in modules_config.json
    config_path = os.environ.get("CONFIG_PATH", "modules_config.json")
    cfg = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
        except Exception:
            pass
            
    # Find matching retailer configuration
    matched_profile = None
    for name in cfg.keys():
        retailer_name = name.replace("_sync", "").replace("_", "")
        clean_payee = payee.lower().replace(" ", "").replace("_", "")
        if retailer_name in clean_payee:
            matched_profile = name
            break
            
    if not matched_profile:
        # Queue New Retailer
        explanation = f"No retailer configuration profile found for payee '{payee}'. Manual login page url config needed."
        details = {"transaction": tx}
        accept = "Enter secure login page URL to launch session scraper."
        reject = "Add merchant to scraper ignore list."
        queue_task(conn, "New Retailer", tx_id, details, explanation, accept, reject)
        return False
        
    # If present, let's run the retailer sync (Normal Retailer Steps Section 6)
    # The actual browser navigation occurs in the Step Interpreter,
    # but the validation engine checks session states and logs tasks:
    
    # 6.1 Cookie state / login check
    # In mock, if we detect session is logged out, we queue Re-Authentication Required:
    # Let's check system logs or state variables
    session_logged_out = False # Mock condition or check
    if session_logged_out:
        explanation = f"Active session state returned logged out for retailer '{matched_profile}'. Re-authentication required."
        details = {"profile": matched_profile}
        accept = "Prompt manual authentication override session."
        reject = "Bypass scraping automation for this retailer."
        queue_task(conn, "Re-Authentication Required", tx_id, details, explanation, accept, reject)
        return False
        
    # 6.3 MFA check
    mfa_present = False # Mock check
    if mfa_present:
        # State one: dispatch alert, queue task, check every 60s
        explanation = f"MFA authentication challenge detected on login page for retailer '{matched_profile}'."
        details = {"profile": matched_profile, "challenge": "OTP"}
        accept = "Submit MFA verification code from dashboard."
        reject = "Halt connection process."
        queue_task(conn, "Re-Authentication Required", tx_id, details, explanation, accept, reject)
        
    # 6.5 & 6.6 matching order history lines
    # Query family_context for Amazon order lines or receipt notes
    cursor.execute("SELECT content FROM family_context WHERE key LIKE 'AMZN_%' OR key LIKE 'Email_%'")
    notes = cursor.fetchall()
    matched_order = False
    for n in notes:
        # Match datetime offset check
        if any(kw in n[0].lower() for kw in payee.lower().split()):
            matched_order = True
            explanation = f"Confirmed Order history items matched bank transaction '{payee}'."
            details = {
                "transaction": tx,
                "order_details": n[0]
            }
            accept = f"Approve itemized category split/mappings from order match details."
            reject = "Manually adjust category allocation."
            queue_task(conn, "Ordered Item Match", tx_id, details, explanation, accept, reject)
            break
            
    return True

# --- Main Engine Runner ---

def run_validation_pipeline(conn):
    # Initialize DB tables just in case
    database.init_db(conn)
    
    transactions = get_eligible_transactions(conn)
    print(f"Validation Engine: Processing {len(transactions)} eligible cleared transactions.")
    
    for tx in transactions:
        tx_id = tx['id']
        payee = tx['payee']
        
        # Rule 7: Rule Mapping Validations
        # Confirm active rule mapping
        cursor = conn.cursor()
        cursor.execute("SELECT id, keyword_pattern, target_category FROM permanent_mapping_rules WHERE ? LIKE '%' || keyword_pattern || '%'", (payee,))
        rule = cursor.fetchone()
        
        if rule:
            # Check if active rule needs adjustment (Rule 7.2)
            # e.g., if category mismatch occurs with historical notes
            # For simplicity, if transaction category differs from rule target, suggest rule adjustment
            rule_id, pattern, rule_cat = rule
            if tx['category'] and tx['category'] != 'Uncategorized' and tx['category'] != rule_cat:
                explanation = f"Existing mapping rule '{pattern}' categorizes as '{rule_cat}', but Simplifi ledger has '{tx['category']}'."
                details = {
                    "transaction": tx,
                    "rule": {"id": rule_id, "pattern": pattern, "category": rule_cat}
                }
                accept = f"Update mapping rule category to '{tx['category']}'."
                reject = "Retain original rule mapping."
                queue_task(conn, "Rule Mapping adjustment", tx_id, details, explanation, accept, reject)
                continue
                
        # Run validations (Bill/Subscription, Transfer, Expense, Income paths)
        classifications = classify_transaction(tx, conn)
        print(f"Transaction {tx_id} ({payee}) classified as: {classifications}")
        
        for classification in classifications:
            if classification == "Bill/Subscription":
                handle_bill_subscription(tx, conn)
            elif classification == "Transfer":
                handle_transfer(tx, conn)
            elif classification == "Expense":
                # Expense path
                handle_expense(tx, conn)
                # Check retailer configs
                run_retailer_check(tx, conn)
            elif classification == "Income":
                handle_income(tx, conn)
                
    # Detect new rule mapping suggestions (Rule 3.22 / 7.2)
    # Look for repeated identical payees (>= 3 times) that have no mapping rule
    cursor.execute("""
        SELECT payee, category, COUNT(*) 
        FROM raw_transactions 
        WHERE payee NOT IN (SELECT keyword_pattern FROM permanent_mapping_rules)
        GROUP BY payee, category
        HAVING COUNT(*) >= 3
    """)
    suggestions = cursor.fetchall()
    for payee, category, count in suggestions:
        explanation = f"Repeated combinations of payee '{payee}' and category '{category}' ({count} times) suggest a new pattern."
        details = {
            "payee": payee,
            "category": category,
            "count": count
        }
        accept = f"Create new permanent rule: Pattern '{payee}' -> Category '{category}'."
        reject = "Dismiss rule suggestion."
        queue_task(conn, "New Rule Mapping", None, details, explanation, accept, reject)
        
    print("Validation pipeline finished successfully.")

import sqlite3
import time
import json
import os
import imaplib
import email
import requests
import hashlib
import random
from email.header import decode_header
from google import genai

def stable_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]

def decode_mime_header(s):
    if not s:
        return ""
    parts = decode_header(s)
    decoded = []
    for payload, charset in parts:
        if isinstance(payload, bytes):
            decoded.append(payload.decode(charset or 'utf-8', errors='replace'))
        else:
            decoded.append(payload)
    return "".join(decoded)

def jitter_sleep(base, variance=1.5):
    sleep_time = max(0.5, base + random.uniform(-variance, variance))
    time.sleep(sleep_time)

def handle_mfa_challenges_if_needed(page, node_name, db_conn):
    mfa_selectors = [
        "input[name*='otp']", "input[id*='otp']",
        "input[name*='mfa']", "input[id*='mfa']",
        "input[name*='code']", "input[id*='code']",
        "input[placeholder*='code']", "input[placeholder*='passcode']"
    ]
    mfa_input = None
    for selector in mfa_selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                mfa_input = loc
                break
        except Exception:
            pass
            
    if mfa_input:
        print(f"MFA Challenge detected on page for node {node_name}. Requesting user input via dashboard.")
        cursor = db_conn.cursor()
        cursor.execute("INSERT INTO mfa_challenges (site_name, challenge_type, status) VALUES (?, 'OTP', 'PENDING')", (node_name,))
        db_conn.commit()
        challenge_id = cursor.lastrowid
        
        # Send Discord notification
        discord_url = os.environ.get("DISCORD_WEBHOOK_URL")
        if discord_url:
            try:
                import requests
                requests.post(
                    discord_url,
                    json={
                        "username": "Digital Accountant",
                        "content": f"⚠️ **MFA Verification Required**\nThe system is waiting for an MFA/OTP code for **{node_name}** to continue syncing. Please visit the Control Panel dashboard to verify."
                    },
                    timeout=10
                )
                print(f"Sent Discord notification for MFA challenge on node {node_name}.")
            except Exception as d_err:
                print(f"Failed sending Discord notification: {d_err}")
        
        start_wait = time.time()
        otp_code = None
        while time.time() - start_wait < 300:
            cursor.execute("SELECT status, code FROM mfa_challenges WHERE id = ?", (challenge_id,))
            row = cursor.fetchone()
            if row and row[0] == 'RESOLVED':
                otp_code = row[1]
                break
            time.sleep(3)
            
        if otp_code:
            print(f"MFA OTP Code received from dashboard: {otp_code}. Filling OTP input.")
            mfa_input.fill(otp_code)
            jitter_sleep(1.5)
            submit_selectors = [
                "button[type='submit']", "input[type='submit']",
                "button:has-text('Submit')", "button:has-text('Verify')",
                "button:has-text('Confirm')", "button:has-text('Continue')"
            ]
            for btn_sel in submit_selectors:
                try:
                    btn = page.locator(btn_sel).first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click()
                        break
                except Exception:
                    pass
            jitter_sleep(5)
            cursor.execute("UPDATE mfa_challenges SET status = 'HANDLED' WHERE id = ?", (challenge_id,))
            db_conn.commit()
        else:
            print("MFA Challenge timed out waiting for user input.")
            raise TimeoutError("MFA Challenge timed out waiting for user input.")

class UniversalInterfaceEngine:
    def __init__(self, config_path=None):
        if not config_path:
            if os.path.exists("/workspace") or os.name != 'nt':
                config_path = "/workspace/modules_config.json"
            else:
                config_path = "modules_config.json"
        self.config_path = config_path
        with open(config_path, "r") as f:
            self.config = json.load(f)

    def execute_node(self, node_name, playwright_context, db_conn):
        if node_name not in self.config:
            print("Error matching operation node")
            return False
        page = None
        cursor = db_conn.cursor()
        node_profile = self.config[node_name]
        try:
            for step in node_profile["steps"]:
                action = step["action"]
                print("Processing action: " + action)
                if action == "goto":
                    if not page:
                        page = playwright_context.new_page()
                    page.goto(step["url"])
                    jitter_sleep(5)
                    if "login" in page.url or "signin" in page.url:
                        print("Authentication block detected. Awaiting manual login via Port 6080.")
                        start_wait = time.time()
                        while time.time() - start_wait < 300:
                            page.wait_for_load_state("load")
                            # Intercept MFA challenges
                            handle_mfa_challenges_if_needed(page, node_name, db_conn)
                            
                            current_url = page.url.lower()
                            if "login" not in current_url and "signin" not in current_url:
                                print(f"Login success detected. Current URL: {page.url}")
                                break
                            time.sleep(2)
                        else:
                            raise TimeoutError("Manual login timed out after 300 seconds.")
                elif action == "wait_for_selector":
                    if not page:
                        raise RuntimeError("No active page found")
                    page.wait_for_selector(step["selector"], timeout=step.get("timeout", 30000))
                elif action == "scrape_transactions":
                    rows = page.locator(step["row_selector"]).all()
                    cols = step["columns"]
                    for row in rows:
                        if (row.locator(cols["date"]).count() == 0 or 
                            row.locator(cols["payee"]).count() == 0 or 
                            row.locator(cols["amount"]).count() == 0 or 
                            row.locator(cols["category"]).count() == 0 or 
                            row.locator(cols["account"]).count() == 0):
                            continue
                        tx_id = row.get_attribute("data-id")
                        if not tx_id:
                            tx_id = stable_hash(row.inner_text())
                        date = row.locator(cols["date"]).inner_text().strip()
                        payee = row.locator(cols["payee"]).inner_text().strip()
                        amount_text = row.locator(cols["amount"]).inner_text()
                        amount = float(amount_text.replace("$", "").replace(",", "").strip())
                        category = row.locator(cols["category"]).inner_text().strip()
                        account = row.locator(cols["account"]).inner_text().strip()
                        cursor.execute("INSERT INTO raw_transactions (id, date, payee, amount, category, account) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET category=excluded.category, amount=excluded.amount", (tx_id, date, payee, amount, category, account))
                    db_conn.commit()
                elif action == "scrape_notes":
                    notes = page.locator(step["note_selector"]).all()
                    for idx, note in enumerate(notes):
                        t_loc = note.locator(step["title_selector"])
                        title = t_loc.inner_text().strip() if t_loc.count() > 0 else "Note_" + str(idx)
                        c_loc = note.locator(step["content_selector"])
                        if c_loc.count() > 0:
                            content = c_loc.inner_text().strip()
                        else:
                            checkboxes = note.locator(step["checkbox_selector"]).all()
                            content = " | ".join([item.inner_text().strip() for item in checkboxes])
                        if title or content:
                            cursor.execute("INSERT INTO family_context (key, content, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET content=excluded.content, updated_at=CURRENT_TIMESTAMP", (title, content))
                    db_conn.commit()
                elif action == "scrape_benefits":
                    cards = page.locator(step["card_selector"]).all()
                    for card in cards:
                        if card.locator(step["title_selector"]).count() == 0 or card.locator(step["balance_selector"]).count() == 0:
                            continue
                        name = card.locator(step["title_selector"]).inner_text().strip()
                        val = card.locator(step["balance_selector"]).inner_text().strip()
                        cursor.execute("INSERT INTO benefits_status (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (name, val))
                    db_conn.commit()
                elif action == "scrape_amazon":
                    cards = page.locator(step["card_selector"]).all()
                    for card in cards:
                        text = card.inner_text()
                        links = card.locator(step["link_selector"]).all()
                        items = " | ".join(set([el.inner_text().strip() for el in links if len(el.inner_text()) > 5]))
                        cursor.execute("INSERT INTO family_context (key, content, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET content=excluded.content, updated_at=CURRENT_TIMESTAMP", ("AMZN_" + stable_hash(text), items))
                    db_conn.commit()
                elif action == "imap_sync":
                    accounts = [
                        {"user": os.environ.get("USER_EMAIL"), "pass": os.environ.get("USER_APP_PASSWORD"), "server": "imap.gmail.com"},
                        {"user": os.environ.get("WIFE_EMAIL"), "pass": os.environ.get("WIFE_APP_PASSWORD"), "server": "imap.gmail.com"}
                    ]
                    for acc in accounts:
                        if not acc["user"] or not acc["pass"]:
                            continue
                        try:
                            mail = imaplib.IMAP4_SSL(acc["server"])
                            mail.login(acc["user"], acc["pass"])
                            mail.select("inbox")
                            status, data = mail.search(None, '(OR FROM "amazon.com" FROM "noreply@target.com")')
                            if status == "OK":
                                for num in data[0].split()[-10:]:
                                    status, msg_data = mail.fetch(num, "(RFC822)")
                                    msg = email.message_from_bytes(msg_data[0][1])
                                    subject = decode_mime_header(msg["Subject"])
                                    cursor.execute("INSERT INTO family_context (key, content, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET content=excluded.content, updated_at=CURRENT_TIMESTAMP", ("Email_" + num.decode(), subject))
                            db_conn.commit()
                            mail.logout()
                        except Exception as m_err:
                            print("IMAP sync failed for account: " + str(acc["user"]) + " with error: " + str(m_err))
                elif action == "run_reconciliation_logic":
                    cursor.execute("SELECT id, date, payee, amount FROM raw_transactions WHERE writeback_status = 'PENDING'")
                    transactions = cursor.fetchall()
                    tx_to_writeback = []
                    for tx_id, dt, payee, amount in transactions:
                        cursor.execute("SELECT target_category FROM permanent_mapping_rules WHERE ? LIKE '%' || keyword_pattern || '%'", (payee,))
                        rule_row = cursor.fetchone()
                        if rule_row:
                            tx_to_writeback.append((tx_id, payee))
                            continue
                        cursor.execute("SELECT loop_state, user_input FROM review_queue WHERE transaction_id = ?", (tx_id,))
                        review_state = cursor.fetchone()
                        if not review_state:
                            cursor.execute("INSERT INTO review_queue (transaction_id, date, payee, amount, loop_state) VALUES (?, ?, ?, ?, 'PENDING_INPUT')", (tx_id, dt, payee, amount))
                            db_conn.commit()
                            self._dispatch_discord("Action Required: Unmapped transaction detected: " + payee + " - $" + str(amount) + ". Add context on port 8080.")
                            continue
                        if review_state[0] == "INPUT_PROVIDED":
                            api_key = os.environ.get("GEMINI_API_KEY")
                            if api_key:
                                client = genai.Client(api_key=api_key)
                                cursor.execute("SELECT content FROM family_context")
                                fam_notes = " ".join([r[0] for r in cursor.fetchall()])
                                cursor.execute("SELECT value FROM benefits_status")
                                ben_notes = " ".join([r[0] for r in cursor.fetchall()])
                                prompt = "Payee: " + payee + " | Amount: " + str(amount) + " | Input: " + str(review_state[1] or '') + " | Context: " + fam_notes + " " + ben_notes + "\nGenerate mapping string format:\nPattern: [keyword] | Category: [Name] | Bill: [Name]"
                                response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
                                proposed = response.text.strip()
                            else:
                                print("GEMINI_API_KEY not set. Using local mock rule generation.")
                                proposed = f"Pattern: {payee} | Category: MockCategory | Bill: None"
                            cursor.execute("UPDATE review_queue SET proposed_rule = ?, loop_state = 'RULE_PROPOSED' WHERE transaction_id = ?", (proposed, tx_id))
                            db_conn.commit()
                            self._dispatch_discord("Rule Ready: Rule verification generated for " + payee + ". Verify on port 8080.")
                    if tx_to_writeback:
                        if not page:
                            page = playwright_context.new_page()
                        page.goto(step["writeback_url"])
                        page.wait_for_selector(step["row_selector"], timeout=60000)
                        for tx_id, payee in tx_to_writeback:
                            try:
                                row_loc = page.locator(step["row_selector"] + "[data-id='" + tx_id + "']")
                                if row_loc.count() > 0:
                                    icon = row_loc.locator(step["icon_sub_selector"])
                                    classes = str(icon.get_attribute("class"))
                                    if step["active_class_marker"] not in classes:
                                        icon.click()
                                        jitter_sleep(1.5)
                                        print("Writeback success: Reconciled " + payee)
                                    else:
                                        print("Writeback already completed on page for: " + payee)
                                    cursor.execute("UPDATE raw_transactions SET writeback_status = 'SUCCESS' WHERE id = ?", (tx_id,))
                                    db_conn.commit()
                            except Exception as wb_err:
                                print("Writeback failed for item " + tx_id + " with error: " + str(wb_err))
                    api_key = os.environ.get("GEMINI_API_KEY")
                    if api_key:
                        client = genai.Client(api_key=api_key)
                        cursor.execute("SELECT date, payee, amount, category FROM raw_transactions ORDER BY date DESC LIMIT 50")
                        tx_data = json.dumps(cursor.fetchall())
                        cursor.execute("SELECT content FROM family_context")
                        ctx_data = json.dumps(cursor.fetchall())
                        cursor.execute("SELECT value FROM benefits_status")
                        hsa_data = json.dumps(cursor.fetchall())
                        report_prompt = "Review recent transactions: " + tx_data + "\nPlans: " + ctx_data + "\nBalances: " + hsa_data + "\nOutput four plain-text sections detailing cash flow trends, medical out-of-pocket items matching HSA availability for reimbursement, forward planning budgeting, and credit account tips. Avoid markdown."
                        rep_res = client.models.generate_content(model="gemini-2.5-flash", contents=report_prompt)
                        report_content = rep_res.text.strip()
                    else:
                        print("GEMINI_API_KEY not set. Using local mock briefing generation.")
                        report_content = "Cash flow trends: Positive.\nHSA medical reimbursement: Check medical payee matches.\nBudget planning: Review categories.\nCredit account tips: Pay statement in full."
                    cursor.execute("INSERT INTO daily_reports (report_content) VALUES (?)", (report_content,))
                    db_conn.commit()
                    self._dispatch_discord("Daily Report Generated:\n" + report_content[:1500])
                elif action == "run_bill_linking_logic":
                    if not page:
                        page = playwright_context.new_page()
                    page.goto(step["url"])
                    page.wait_for_selector(step["bill_selector"], timeout=60000)
                    bill_reminders = page.locator(step["bill_selector"]).all()
                    for bill in bill_reminders:
                        biller_name = bill.locator(step["biller_selector"]).inner_text().strip()
                        bill_amount_str = bill.locator(step["amount_selector"]).inner_text().replace("$", "").replace(",", "").strip()
                        bill_amount = float(bill_amount_str)
                        cursor.execute("SELECT id FROM raw_transactions WHERE amount = ? AND category != 'Linked Bill' LIMIT 1", (bill_amount,))
                        matched_tx = cursor.fetchone()
                        if matched_tx:
                            target_tx_id = matched_tx[0]
                            bill.locator(step["options_selector"]).click()
                            time.sleep(1)
                            page.locator(step["link_option_selector"]).click()
                            time.sleep(2)
                            search_input = page.locator(step["search_input_selector"])
                            search_input.fill(target_tx_id)
                            time.sleep(1)
                            target_row_checkbox = page.locator(".link-matching-row[data-tx-id='" + target_tx_id + "'] input[type='checkbox']")
                            if target_row_checkbox.count() > 0:
                                target_row_checkbox.check()
                                time.sleep(1)
                                page.locator(step["confirm_button_selector"]).click()
                                time.sleep(3)
                            else:
                                page.locator(step["cancel_button_selector"]).click()
                                time.sleep(1)
            return True
        except Exception as e:
            raise e
        finally:
            if page:
                page.close()

    def _dispatch_discord(self, message):
        url = os.environ.get("DISCORD_WEBHOOK_URL")
        if url:
            try:
                requests.post(url, json={"username": "Digital Accountant", "content": message}, timeout=10)
            except Exception as e:
                print("Discord dispatch failure: " + str(e))

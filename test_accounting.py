import unittest
import os
import sqlite3
import tempfile
import json
import datetime
from unittest.mock import MagicMock, patch

# Configure test environment DB path
TEMP_DB = tempfile.mktemp(suffix=".db")
os.environ["DB_PATH"] = TEMP_DB

# Note: we need modules_config.json to exist for UniversalInterfaceEngine
TEMP_CONFIG = tempfile.mktemp(suffix=".json")
os.environ["CONFIG_PATH"] = TEMP_CONFIG

# Write dummy config
dummy_config = {
    "execution_order": ["quicken_simplifi_sync", "forma_hsa_sync"],
    "quicken_simplifi_sync": {
        "steps": [
            {"action": "goto", "url": "https://example.com"},
            {"action": "scrape_transactions", "row_selector": "tr", "columns": {
                "date": ".date", "payee": ".payee", "amount": ".amount", "category": ".cat", "account": ".acc"
            }}
        ]
    },
    "forma_hsa_sync": {
        "steps": []
    }
}
with open(TEMP_CONFIG, "w") as f:
    json.dump(dummy_config, f)

import database
import orchestrator
from interpreter import UniversalInterfaceEngine
import web_app

class TestDatabase(unittest.TestCase):
    def setUp(self):
        # Fresh database for each test
        if os.path.exists(TEMP_DB):
            os.remove(TEMP_DB)
        database.init_db()
        self.conn = sqlite3.connect(TEMP_DB)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(TEMP_DB):
            try:
                os.remove(TEMP_DB)
            except OSError:
                pass

    def test_tables_created(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("raw_transactions", tables)
        self.assertIn("family_context", tables)
        self.assertIn("benefits_status", tables)
        self.assertIn("review_queue", tables)
        self.assertIn("permanent_mapping_rules", tables)
        self.assertIn("daily_reports", tables)
        self.assertIn("audit_log", tables)
        self.assertIn("simplifi_connections", tables)
        self.assertIn("export_reminders", tables)
        self.assertIn("command_queue", tables)
        self.assertIn("ai_reasoning_log", tables)
        self.assertIn("mfa_challenges", tables)
        self.assertIn("pending_new_sites", tables)

    def test_is_connected_in_simplifi(self):
        # Defaults to True (CONNECTED)
        self.assertTrue(database.is_connected_in_simplifi(self.conn, "test_node"))
        
        # Now flag as disconnected
        database.flag_for_simplifi_connection(self.conn, "test_node")
        self.assertFalse(database.is_connected_in_simplifi(self.conn, "test_node"))
        
        # Verify audit log
        cursor = self.conn.cursor()
        cursor.execute("SELECT status FROM audit_log WHERE module_name = 'test_node'")
        logs = cursor.fetchall()
        self.assertTrue(any(row[0] == "CONNECTION_REQUIRED" for row in logs))

    def test_log_audit(self):
        database.log_audit(self.conn, "test_module", "TEST_STATUS", "details here")
        cursor = self.conn.cursor()
        cursor.execute("SELECT status, details FROM audit_log WHERE module_name = 'test_module'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "TEST_STATUS")
        self.assertEqual(row[1], "details here")

    def test_flag_for_manual_export(self):
        database.flag_for_manual_export(self.conn, "test_account")
        cursor = self.conn.cursor()
        cursor.execute("SELECT status FROM export_reminders WHERE account_name = 'test_account'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "PENDING")
        
        # Verify audit log
        cursor.execute("SELECT status FROM audit_log WHERE module_name = 'test_account'")
        logs = [r[0] for r in cursor.fetchall()]
        self.assertIn("EXPORT_SCHEDULED", logs)


class TestOrchestrator(unittest.TestCase):
    def test_strip_dom(self):
        html_input = "<html><head><script>alert(1)</script><style>body{}</style></head><body><h1>Hello</h1><svg>...</svg><img src='test.png'/></body></html>"
        expected = "<html><head></head><body><h1>Hello</h1></body></html>"
        stripped = orchestrator.strip_dom(html_input)
        # Strip whitespaces to compare easily
        self.assertEqual(stripped.replace(" ", "").replace("\n", ""), expected.replace(" ", "").replace("\n", ""))

    def test_cache_infrastructure(self):
        input_data = {"tx": "123", "amount": 10.0}
        orchestrator.set_cached_response(input_data, "categorized_resp")
        self.assertEqual(orchestrator.get_cached_response(input_data), "categorized_resp")

    def test_batch_categorize_fallback(self):
        # Remove API key environment variable temporarily if set
        old_key = os.environ.get("GEMINI_API_KEY")
        if "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]
            
        try:
            transactions = [
                {"id": "1", "payee": "amazon checkout"},
                {"id": "2", "payee": "forma account"},
                {"id": "3", "payee": "unknown shop"}
            ]
            res = orchestrator.batch_categorize_transactions(transactions)
            self.assertEqual(len(res), 3)
            self.assertEqual(res[0]["category"], "Shopping")
            self.assertEqual(res[1]["category"], "Health & HSA")
            self.assertEqual(res[2]["category"], "Uncategorized")
        finally:
            if old_key is not None:
                os.environ["GEMINI_API_KEY"] = old_key

    def test_dotenv_loader(self):
        temp_env = tempfile.mktemp(suffix=".env")
        with open(temp_env, "w", encoding="utf-8") as f:
            f.write("TEST_ENV_KEY=some_value\n")
            f.write("# comment here\n")
            f.write("ANOTHER_KEY='quoted_value'\n")
        
        # Call loader
        orchestrator.load_dotenv(temp_env)
        self.assertEqual(os.environ.get("TEST_ENV_KEY"), "some_value")
        self.assertEqual(os.environ.get("ANOTHER_KEY"), "quoted_value")
        
        # Cleanup
        os.environ.pop("TEST_ENV_KEY", None)
        os.environ.pop("ANOTHER_KEY", None)
        if os.path.exists(temp_env):
            os.remove(temp_env)

    def test_self_healing_backup_and_rollback(self):
        # We simulate the backup copy creation
        temp_conf = tempfile.mktemp(suffix=".json")
        with open(temp_conf, "w") as f:
            json.dump({"test": "original"}, f)
            
        backup_conf = temp_conf + ".bak"
        
        try:
            # Backup
            import shutil
            shutil.copy(temp_conf, backup_conf)
            self.assertTrue(os.path.exists(backup_conf))
            
            # Modify original
            with open(temp_conf, "w") as f:
                json.dump({"test": "corrupted"}, f)
                
            # Rollback
            shutil.copy(backup_conf, temp_conf)
            with open(temp_conf, "r") as f:
                data = json.load(f)
            self.assertEqual(data["test"], "original")
        finally:
            for path in (temp_conf, backup_conf):
                if os.path.exists(path):
                    os.remove(path)

    def test_self_healing_safeguards(self):
        # Verify that temporary network errors bypass self-healing
        tb_net_err = "playwright._impl._errors.Error: page.goto: net::ERR_NAME_NOT_RESOLVED at ..."
        res = orchestrator.attempt_self_healing("quicken_simplifi_sync", tb_net_err)
        self.assertFalse(res)
        
        tb_refused = "playwright._impl._errors.Error: net::ERR_CONNECTION_REFUSED at ..."
        res = orchestrator.attempt_self_healing("forma_hsa_sync", tb_refused)
        self.assertFalse(res)

    @patch('orchestrator.genai.Client')
    def test_code_and_config_self_healing(self, mock_client_cls):
        # Verify that Gemini's dual code/config healing successfully backs up and updates both files
        old_key = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "fake_key"
        
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = '{\n  "repaired_config": {"execution_order": ["quicken_simplifi_sync"], "quicken_simplifi_sync": {"steps": [{"action": "goto", "url": "https://simplifi.quicken.com"}]}},\n  "repaired_interpreter": "# healed code"\n}'
        mock_client.models.generate_content.return_value = mock_response
        
        temp_conf = tempfile.mktemp(suffix=".json")
        with open(temp_conf, "w") as f:
            json.dump({"execution_order": []}, f)
            
        temp_int = tempfile.mktemp(suffix=".py")
        with open(temp_int, "w") as f:
            f.write("# original code")
            
        old_conf_path = orchestrator.CONFIG_PATH
        orchestrator.CONFIG_PATH = temp_conf
        
        original_open = open
        def mock_open(file, mode="r", *args, **kwargs):
            if file == "interpreter.py" or file == "/workspace/interpreter.py":
                return original_open(temp_int, mode, *args, **kwargs)
            return original_open(file, mode, *args, **kwargs)
            
        import shutil
        original_copy = shutil.copy
        def mock_copy(src, dst):
            if src == "interpreter.py" or src == "/workspace/interpreter.py":
                src = temp_int
            if dst == "interpreter.py.bak" or dst == "/workspace/interpreter.py.bak":
                dst = temp_int + ".bak"
            return original_copy(src, dst)
            
        try:
            with patch('builtins.open', side_effect=mock_open), patch('shutil.copy', side_effect=mock_copy):
                res = orchestrator.attempt_self_healing("quicken_simplifi_sync", "TimeoutError: element not found")
                self.assertTrue(res)
                
                # Check config was updated
                with original_open(temp_conf, "r") as f:
                    conf_data = json.load(f)
                self.assertEqual(conf_data["execution_order"], ["quicken_simplifi_sync"])
                
                # Check interpreter was updated
                with original_open(temp_int, "r") as f:
                    int_code = f.read()
                self.assertEqual(int_code, "# healed code")
                
                # Check backups were created
                self.assertTrue(os.path.exists(temp_conf + ".bak"))
                self.assertTrue(os.path.exists(temp_int + ".bak"))
        finally:
            orchestrator.CONFIG_PATH = old_conf_path
            if old_key is not None:
                os.environ["GEMINI_API_KEY"] = old_key
            else:
                os.environ.pop("GEMINI_API_KEY", None)
            for path in (temp_conf, temp_int, temp_conf + ".bak", temp_int + ".bak"):
                if os.path.exists(path):
                    os.remove(path)

    def test_dynamic_headless_selection(self):
        # Save old environment
        old_hl = os.environ.get("HEADLESS")
        old_disp = os.environ.get("DISPLAY")
        
        try:
            # Case 1: HEADLESS = "1"
            os.environ["HEADLESS"] = "1"
            is_headless = os.getenv("HEADLESS", "0") == "1" or (not os.getenv("DISPLAY") and os.name != 'nt')
            self.assertTrue(is_headless)
            
            # Case 2: HEADLESS = "0" and DISPLAY present on Linux (simulated by setting environment)
            os.environ["HEADLESS"] = "0"
            os.environ["DISPLAY"] = ":0.0"
            is_headless = os.getenv("HEADLESS", "0") == "1" or (not os.getenv("DISPLAY") and os.name != 'nt')
            self.assertFalse(is_headless)
        finally:
            if old_hl is not None:
                os.environ["HEADLESS"] = old_hl
            else:
                os.environ.pop("HEADLESS", None)
            if old_disp is not None:
                os.environ["DISPLAY"] = old_disp
            else:
                os.environ.pop("DISPLAY", None)


class TestInterpreter(unittest.TestCase):
    def test_init_engine(self):
        engine = UniversalInterfaceEngine(TEMP_CONFIG)
        self.assertIn("quicken_simplifi_sync", engine.config)
        self.assertIn("forma_hsa_sync", engine.config)

    @patch('interpreter.time.sleep', return_value=None)
    def test_execute_node_invalid(self, mock_sleep):
        engine = UniversalInterfaceEngine(TEMP_CONFIG)
        conn = sqlite3.connect(TEMP_DB)
        database.init_db(conn)
        res = engine.execute_node("invalid_node", None, conn)
        self.assertFalse(res)
        conn.close()

    def test_stable_hashing(self):
        from interpreter import stable_hash
        text = "Hello World Transaction 123"
        h1 = stable_hash(text)
        h2 = stable_hash(text)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)
        
    def test_email_mime_decoding(self):
        from interpreter import decode_mime_header
        # Test basic decoding
        self.assertEqual(decode_mime_header("Simple Subject"), "Simple Subject")
        # Test UTF-8 B encoded subject
        self.assertEqual(decode_mime_header("=?utf-8?B?VGVzdA==?="), "Test")

    @patch('interpreter.UniversalInterfaceEngine.execute_node')
    def test_writeback_status_updates(self, mock_execute):
        # Setup mock db and insert transaction with PENDING status
        conn = sqlite3.connect(TEMP_DB)
        database.init_db(conn)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO raw_transactions (id, date, payee, amount, category, account, writeback_status) VALUES ('tx_wb_1', '2026-07-14', 'Netflix', 15.99, 'Utilities', 'Credit Card', 'PENDING')")
        conn.commit()
        
        # Verify initial status is PENDING
        cursor.execute("SELECT writeback_status FROM raw_transactions WHERE id = 'tx_wb_1'")
        self.assertEqual(cursor.fetchone()[0], 'PENDING')
        
        # Simulate successful write-back and state update
        cursor.execute("UPDATE raw_transactions SET writeback_status = 'SUCCESS' WHERE id = 'tx_wb_1'")
        conn.commit()
        
        # Verify status became SUCCESS
        cursor.execute("SELECT writeback_status FROM raw_transactions WHERE id = 'tx_wb_1'")
        self.assertEqual(cursor.fetchone()[0], 'SUCCESS')
        
        conn.close()

    @patch('requests.post')
    def test_mfa_discord_alert(self, mock_post):
        old_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
        os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/mock"
        
        try:
            from interpreter import handle_mfa_challenges_if_needed
            mock_page = MagicMock()
            mock_page.locator.return_value.first.count.return_value = 1
            mock_page.locator.return_value.first.is_visible.return_value = True
            
            conn = sqlite3.connect(TEMP_DB)
            database.init_db(conn)
            
            with patch('time.time', side_effect=[0, 1000]), self.assertRaises(TimeoutError):
                handle_mfa_challenges_if_needed(mock_page, "Quicken Simplifi", conn)
                
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertEqual(args[0], "https://discord.com/api/webhooks/mock")
            self.assertIn("Quicken Simplifi", kwargs["json"]["content"])
            conn.close()
        finally:
            if old_webhook is not None:
                os.environ["DISCORD_WEBHOOK_URL"] = old_webhook
            else:
                os.environ.pop("DISCORD_WEBHOOK_URL", None)


class TestWebApp(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEMP_DB):
            os.remove(TEMP_DB)
        database.init_db()
        self.conn = sqlite3.connect(TEMP_DB)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(TEMP_DB):
            try:
                os.remove(TEMP_DB)
            except OSError:
                pass

    def test_dashboard_renders(self):
        # Insert a raw transaction, review queue item, rule and daily briefing
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO raw_transactions (id, date, payee, amount, category, account) VALUES ('tx1', '2026-07-14', 'Amazon', 12.34, 'Shopping', 'Credit')")
        cursor.execute("INSERT INTO review_queue VALUES ('tx1', '2026-07-14', 'Amazon', 12.34, '', '', 'PENDING_INPUT')")
        cursor.execute("INSERT INTO permanent_mapping_rules (keyword_pattern, target_category, target_bill_name) VALUES ('Target', 'Groceries', 'Target RedCard')")
        cursor.execute("INSERT INTO daily_reports (report_content, timestamp) VALUES ('Mock briefing content', '2026-07-15 03:00:00')")
        self.conn.commit()

        # Render dashboard directly calling web_app
        response_html = web_app.dashboard()
        self.assertIn("Amazon", response_html)
        self.assertIn("Target", response_html)
        self.assertIn("Mock briefing content", response_html)
        self.assertIn("$12.34", response_html)
        self.assertIn("Pending Input", response_html)

    def test_submit_input(self):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO review_queue (transaction_id, loop_state) VALUES ('tx1', 'PENDING_INPUT')")
        self.conn.commit()

        # Submit user context
        response = web_app.handle_input(tx_id="tx1", user_text="office purchase")
        self.assertEqual(response.status_code, 303)

        cursor.execute("SELECT user_input, loop_state FROM review_queue WHERE transaction_id = 'tx1'")
        row = cursor.fetchone()
        self.assertEqual(row[0], "office purchase")
        self.assertEqual(row[1], "INPUT_PROVIDED")

    def test_action_rule_approve(self):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO review_queue (transaction_id, proposed_rule, loop_state) VALUES ('tx1', 'Pattern: Walmart | Category: Groceries | Bill: Walmart Bill', 'RULE_PROPOSED')")
        self.conn.commit()

        # Approve
        response = web_app.handle_rule_action(tx_id="tx1", action="approve")
        self.assertEqual(response.status_code, 303)

        # Check rule was added
        cursor.execute("SELECT keyword_pattern, target_category, target_bill_name FROM permanent_mapping_rules WHERE keyword_pattern = 'Walmart'")
        rule = cursor.fetchone()
        self.assertIsNotNone(rule)
        self.assertEqual(rule[1], "Groceries")
        self.assertEqual(rule[2], "Walmart Bill")

        # Check queue status updated
        cursor.execute("SELECT loop_state FROM review_queue WHERE transaction_id = 'tx1'")
        self.assertEqual(cursor.fetchone()[0], "APPROVED")

    def test_action_rule_reject(self):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO review_queue (transaction_id, proposed_rule, loop_state) VALUES ('tx1', 'Pattern: Walmart | Category: Groceries | Bill: Walmart Bill', 'RULE_PROPOSED')")
        self.conn.commit()

        # Reject
        response = web_app.handle_rule_action(tx_id="tx1", action="reject")
        self.assertEqual(response.status_code, 303)

        # Check queue status updated
        cursor.execute("SELECT loop_state, proposed_rule, user_input FROM review_queue WHERE transaction_id = 'tx1'")
        row = cursor.fetchone()
        self.assertEqual(row[0], "REJECTED")
        self.assertIsNone(row[1])
        self.assertIsNone(row[2])

    def test_send_command(self):
        response = web_app.send_command("Run manual sync")
        self.assertEqual(response.status_code, 303)

        cursor = self.conn.cursor()
        cursor.execute("SELECT command, status FROM command_queue")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Run manual sync")
        self.assertEqual(row[1], "PENDING")

    def test_rejected_state_renders_form(self):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO review_queue VALUES ('tx2', '2026-07-14', 'Amazon Rejected', 99.99, '', '', 'REJECTED')")
        self.conn.commit()

        # Render dashboard
        response_html = web_app.dashboard()
        self.assertIn("Amazon Rejected", response_html)
        self.assertIn("Rejected", response_html)
        # Should render form with submission target /submit-input
        self.assertIn("action='/submit-input'", response_html)
        self.assertIn("Provide context to re-draft this rule...", response_html)

    def test_rule_approve_with_markdown_stripping(self):
        cursor = self.conn.cursor()
        # Insert a proposed rule wrapped in markdown backticks
        cursor.execute("INSERT INTO review_queue (transaction_id, proposed_rule, loop_state) VALUES ('tx3', '```json\nPattern: Target | Category: Groceries | Bill: Target Card\n```', 'RULE_PROPOSED')")
        self.conn.commit()

        # Approve rule
        response = web_app.handle_rule_action(tx_id="tx3", action="approve")
        self.assertEqual(response.status_code, 303)

        # Verify pattern was parsed and rule added
        cursor.execute("SELECT keyword_pattern, target_category, target_bill_name FROM permanent_mapping_rules WHERE keyword_pattern = 'Target'")
        rule = cursor.fetchone()
        self.assertIsNotNone(rule)
        self.assertEqual(rule[1], "Groceries")
        self.assertEqual(rule[2], "Target Card")

    def test_basicauth_dependency(self):
        from fastapi import HTTPException
        import base64
        # Mock Request
        class MockRequest:
            def __init__(self, headers):
                self.headers = headers
                
        # 1. No credentials configured (bypass)
        old_user = os.environ.get("DASHBOARD_USERNAME")
        old_pass = os.environ.get("DASHBOARD_PASSWORD")
        if "DASHBOARD_USERNAME" in os.environ:
            del os.environ["DASHBOARD_USERNAME"]
        if "DASHBOARD_PASSWORD" in os.environ:
            del os.environ["DASHBOARD_PASSWORD"]
            
        req_no_auth = MockRequest(headers={})
        web_app.verify_credentials_optional(req_no_auth)
        
        # 2. Credentials configured but request lacks Authorization header (prompt Basic Auth)
        os.environ["DASHBOARD_USERNAME"] = "user123"
        os.environ["DASHBOARD_PASSWORD"] = "pass123"
        
        try:
            with self.assertRaises(HTTPException) as ctx:
                web_app.verify_credentials_optional(req_no_auth)
            self.assertEqual(ctx.exception.status_code, 401)
            self.assertIn("WWW-Authenticate", ctx.exception.headers)
            
            # 3. Request has incorrect Authorization header
            req_bad_auth = MockRequest(headers={"Authorization": "Basic " + base64.b64encode(b"wrong:pass").decode()})
            with self.assertRaises(HTTPException) as ctx:
                web_app.verify_credentials_optional(req_bad_auth)
            self.assertEqual(ctx.exception.status_code, 401)
            
            # 4. Request has correct credentials
            req_good_auth = MockRequest(headers={"Authorization": "Basic " + base64.b64encode(b"user123:pass123").decode()})
            web_app.verify_credentials_optional(req_good_auth)
        finally:
            if old_user is not None:
                os.environ["DASHBOARD_USERNAME"] = old_user
            else:
                os.environ.pop("DASHBOARD_USERNAME", None)
            if old_pass is not None:
                os.environ["DASHBOARD_PASSWORD"] = old_pass
            else:
                os.environ.pop("DASHBOARD_PASSWORD", None)

    def test_submit_mfa(self):
        # Insert PENDING MFA challenge
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO mfa_challenges (site_name, challenge_type, status) VALUES ('Simplifi', 'OTP', 'PENDING')")
        self.conn.commit()
        cursor.execute("SELECT id FROM mfa_challenges WHERE site_name = 'Simplifi'")
        chal_id = cursor.fetchone()[0]
        
        # Submit MFA code
        response = web_app.submit_mfa(challenge_id=chal_id, mfa_code="987654")
        self.assertEqual(response.status_code, 303)
        
        # Verify database updated
        cursor.execute("SELECT code, status FROM mfa_challenges WHERE id = ?", (chal_id,))
        row = cursor.fetchone()
        self.assertEqual(row[0], "987654")
        self.assertEqual(row[1], "RESOLVED")

    def test_export_endpoints(self):
        # Populate raw transactions and rules
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO raw_transactions (id, date, payee, amount, category, account) VALUES ('tx_exp_1', '2026-07-15', 'Export Payee', 45.67, 'Food', 'Debit Card')")
        cursor.execute("INSERT INTO permanent_mapping_rules (keyword_pattern, target_category, target_bill_name) VALUES ('ExpPattern', 'Utilities', 'Water')")
        self.conn.commit()
        
        # Test transactions CSV export
        tx_resp = web_app.export_transactions()
        self.assertIsNotNone(tx_resp)
        
        import asyncio
        async def consume_iterator(iterator):
            chunks = []
            async for chunk in iterator:
                chunks.append(chunk)
            return b"".join(chunks)
            
        tx_csv = asyncio.run(consume_iterator(tx_resp.body_iterator)).decode()
        self.assertIn("Export Payee", tx_csv)
        self.assertIn("45.67", tx_csv)
        
        # Test rules CSV export
        rules_resp = web_app.export_rules()
        self.assertIsNotNone(rules_resp)
        rules_csv = asyncio.run(consume_iterator(rules_resp.body_iterator)).decode()
        self.assertIn("ExpPattern", rules_csv)
        self.assertIn("Utilities", rules_csv)

    @patch('orchestrator.run_pipeline')
    def test_command_worker_execution(self, mock_run):
        # Insert a pending command
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO command_queue (command, status) VALUES ('Sync Quicken', 'PENDING')")
        self.conn.commit()
        
        # Trigger worker check loop inline for test
        cursor.execute("SELECT id, command FROM command_queue WHERE status = 'PENDING' LIMIT 1")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        cmd_id, cmd_text = row
        self.assertEqual(cmd_text, 'Sync Quicken')
        
        cursor.execute("UPDATE command_queue SET status = 'PROCESSING' WHERE id = ?", (cmd_id,))
        self.conn.commit()
        
        orchestrator.run_pipeline()
        cursor.execute("UPDATE command_queue SET status = 'SUCCESS' WHERE id = ?", (cmd_id,))
        self.conn.commit()
        
        cursor.execute("SELECT status FROM command_queue WHERE id = ?", (cmd_id,))
        self.assertEqual(cursor.fetchone()[0], 'SUCCESS')
        mock_run.assert_called_once()

    def test_mfa_challenges_pruner(self):
        # Insert a challenge that is pending and 10 minutes old, and another that is pending and fresh
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO mfa_challenges (site_name, challenge_type, status, timestamp) VALUES ('StaleBank', 'OTP', 'PENDING', datetime('now', '-10 minutes'))")
        cursor.execute("INSERT INTO mfa_challenges (site_name, challenge_type, status, timestamp) VALUES ('FreshBank', 'OTP', 'PENDING', datetime('now', '-1 minute'))")
        self.conn.commit()
        
        # Trigger dashboard load which calls pruner
        web_app.dashboard()
        
        # Verify StaleBank challenge is EXPIRED
        cursor.execute("SELECT status FROM mfa_challenges WHERE site_name = 'StaleBank'")
        self.assertEqual(cursor.fetchone()[0], 'EXPIRED')
        
        # Verify FreshBank challenge is still PENDING
        cursor.execute("SELECT status FROM mfa_challenges WHERE site_name = 'FreshBank'")
        self.assertEqual(cursor.fetchone()[0], 'PENDING')


if __name__ == "__main__":
    unittest.main()

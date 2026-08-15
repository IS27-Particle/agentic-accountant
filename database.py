import sqlite3
import os
import datetime

# Determine DB_PATH dynamically to support host and docker execution
DB_PATH = os.environ.get("DB_PATH")
if not DB_PATH:
    if os.path.exists("/workspace") and os.path.isdir("/workspace"):
        DB_PATH = "/workspace/shared_state.db"
    else:
        DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared_state.db")

def init_db(conn=None):
    should_close = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        should_close = True
    
    cursor = conn.cursor()
    
    # 1. raw_transactions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_transactions (
            id TEXT PRIMARY KEY,
            date TEXT,
            payee TEXT,
            amount REAL,
            category TEXT,
            account TEXT,
            writeback_status TEXT DEFAULT 'PENDING',
            cleared TEXT DEFAULT 'CLEARED'
        )
    """)
    cursor.execute("PRAGMA table_info(raw_transactions)")
    columns = [row[1] for row in cursor.fetchall()]
    if columns and "writeback_status" not in columns:
        cursor.execute("ALTER TABLE raw_transactions ADD COLUMN writeback_status TEXT DEFAULT 'PENDING'")
    if columns and "cleared" not in columns:
        cursor.execute("ALTER TABLE raw_transactions ADD COLUMN cleared TEXT DEFAULT 'CLEARED'")
    
    # 2. quicken_bills
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quicken_bills (
            id TEXT PRIMARY KEY,
            biller_name TEXT,
            due_date TEXT,
            amount REAL,
            linked_transaction_id TEXT,
            status TEXT DEFAULT 'UNPAID'
        )
    """)
    
    # 3. google_keep_notes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS google_keep_notes (
            id TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            has_checkboxes INTEGER DEFAULT 0,
            synced_at TEXT
        )
    """)
    
    # 4. forma_hsa_benefits
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS forma_hsa_benefits (
            id TEXT PRIMARY KEY,
            benefit_title TEXT,
            balance REAL,
            last_updated TEXT
        )
    """)
    
    # 5. amazon_orders
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS amazon_orders (
            id TEXT PRIMARY KEY,
            order_date TEXT,
            total_amount REAL,
            items_description TEXT,
            linked_transaction_id TEXT
        )
    """)
    
    # 6. classification_rules
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classification_rules (
            id TEXT PRIMARY KEY,
            rule_name TEXT,
            conditions_json TEXT,
            target_category TEXT,
            confidence REAL DEFAULT 1.0,
            active INTEGER DEFAULT 1
        )
    """)
    
    # 7. reconciliation_log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_log (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            source_system TEXT,
            action_type TEXT,
            details_json TEXT,
            status TEXT
        )
    """)
    
    # 8. system_logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            level TEXT,
            module TEXT,
            message TEXT
        )
    """)
    
    # 9. audit_trail
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            table_name TEXT,
            record_id TEXT,
            action TEXT,
            old_values TEXT,
            new_values TEXT,
            user_or_process TEXT
        )
    """)
    
    # 10. execution_queue
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS execution_queue (
            task_id TEXT PRIMARY KEY,
            task_name TEXT NOT NULL,
            payload TEXT,
            priority INTEGER DEFAULT 10,
            status TEXT DEFAULT 'QUEUED',
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            error_message TEXT
        )
    """)
    
    # 11. task_run_logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_run_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            log_message TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    
    # 12. browser_sync_status
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS browser_sync_status (
            node_name TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error_message TEXT
        )
    """)

    # 13. chart_of_accounts (Assets, Liabilities, Equity, Revenue, Expenses)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chart_of_accounts (
            account_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL, -- ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE
            normal_balance TEXT NOT NULL, -- DEBIT, CREDIT
            balance REAL DEFAULT 0.0,
            created_at TEXT
        )
    """)

    # 14. double_entry_ledger (Balanced Journal Entries)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS double_entry_ledger (
            entry_id TEXT PRIMARY KEY,
            transaction_id TEXT,
            date TEXT NOT NULL,
            description TEXT,
            debit_account TEXT NOT NULL,
            credit_account TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'POSTED',
            created_at TEXT NOT NULL,
            FOREIGN KEY (debit_account) REFERENCES chart_of_accounts (account_id),
            FOREIGN KEY (credit_account) REFERENCES chart_of_accounts (account_id)
        )
    """)

    # 15. financial_reports
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS financial_reports (
            report_id TEXT PRIMARY KEY,
            report_type TEXT NOT NULL, -- BALANCE_SHEET, INCOME_STATEMENT, CASH_FLOW
            period_start TEXT,
            period_end TEXT,
            data_json TEXT NOT NULL,
            generated_at TEXT NOT NULL
        )
    """)
    
    conn.commit()
    if should_close:
        conn.close()

if __name__ == "__main__":
    init_db()
    print("Database schema successfully initialized.")

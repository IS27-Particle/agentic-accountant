import sqlite3
import os
import datetime

# Determine DB_PATH dynamically to support host and docker execution
DB_PATH = os.environ.get("DB_PATH")
if not DB_PATH:
    if os.path.exists("/workspace") or os.name != 'nt':
        DB_PATH = "/workspace/shared_state.db"
    else:
        DB_PATH = "shared_state.db"

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
            writeback_status TEXT DEFAULT 'PENDING'
        )
    """)
    # Migration: Add writeback_status column if it doesn't exist in an existing table
    cursor.execute("PRAGMA table_info(raw_transactions)")
    columns = [row[1] for row in cursor.fetchall()]
    if columns and "writeback_status" not in columns:
        cursor.execute("ALTER TABLE raw_transactions ADD COLUMN writeback_status TEXT DEFAULT 'PENDING'")
    
    # 2. family_context
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS family_context (
            key TEXT PRIMARY KEY,
            content TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 3. benefits_status
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS benefits_status (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 4. review_queue
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS review_queue (
            transaction_id TEXT PRIMARY KEY,
            date TEXT,
            payee TEXT,
            amount REAL,
            user_input TEXT,
            proposed_rule TEXT,
            loop_state TEXT
        )
    """)
    
    # 5. permanent_mapping_rules
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS permanent_mapping_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword_pattern TEXT,
            target_category TEXT,
            target_bill_name TEXT
        )
    """)
    
    # 6. daily_reports
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 7. audit_log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            module_name TEXT,
            status TEXT,
            details TEXT
        )
    """)
    
    # 8. simplifi_connections
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS simplifi_connections (
            account_name TEXT PRIMARY KEY,
            status TEXT,
            last_checked DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 9. export_reminders
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS export_reminders (
            account_name TEXT PRIMARY KEY,
            last_export_date TEXT,
            status TEXT DEFAULT 'PENDING'
        )
    """)
    
    # 10. command_queue (used by web_app.py for Direct Agent Commands)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS command_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT,
            status TEXT DEFAULT 'PENDING',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 11. ai_reasoning_log (used by orchestrator.py for self-healing records)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_reasoning_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt TEXT,
            response TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 12. mfa_challenges (referenced in architecture spec)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mfa_challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT,
            challenge_type TEXT,
            code TEXT,
            status TEXT DEFAULT 'PENDING',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migration: Add code column if it doesn't exist
    cursor.execute("PRAGMA table_info(mfa_challenges)")
    mfa_cols = [row[1] for row in cursor.fetchall()]
    if mfa_cols and "code" not in mfa_cols:
        cursor.execute("ALTER TABLE mfa_challenges ADD COLUMN code TEXT")

    # 13. pending_new_sites (referenced in architecture spec)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_new_sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            status TEXT DEFAULT 'PENDING',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 14. email_accounts (dynamic multiple email configs)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_address TEXT UNIQUE,
            status TEXT DEFAULT 'ACTIVE',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 15. budget_sites (dynamic custom budget portal target)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS budget_sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT UNIQUE,
            url TEXT,
            status TEXT DEFAULT 'ACTIVE',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    if should_close:
        conn.close()
        print("Database initialization complete.")

def is_connected_in_simplifi(conn, account_name):
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM simplifi_connections WHERE account_name = ?", (account_name,))
    row = cursor.fetchone()
    if row is None:
        # Default to CONNECTED so processing goes through unless explicitly set to DISCONNECTED
        cursor.execute("INSERT INTO simplifi_connections (account_name, status) VALUES (?, 'CONNECTED')", (account_name,))
        conn.commit()
        return True
    return row[0] == 'CONNECTED'

def flag_for_simplifi_connection(conn, account_name):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO simplifi_connections (account_name, status, last_checked)
        VALUES (?, 'DISCONNECTED', CURRENT_TIMESTAMP)
        ON CONFLICT(account_name) DO UPDATE SET status='DISCONNECTED', last_checked=CURRENT_TIMESTAMP
    """, (account_name,))
    cursor.execute("""
        INSERT INTO audit_log (module_name, status, details)
        VALUES (?, 'CONNECTION_REQUIRED', ?)
    """, (account_name, f"Simplifi connection required for {account_name}"))
    conn.commit()

def log_audit(conn, module_name, status, details=None):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO audit_log (module_name, status, details)
        VALUES (?, ?, ?)
    """, (module_name, status, details))
    conn.commit()

def flag_for_manual_export(conn, account_name):
    cursor = conn.cursor()
    today_str = datetime.date.today().isoformat()
    cursor.execute("""
        INSERT INTO export_reminders (account_name, last_export_date, status)
        VALUES (?, ?, 'PENDING')
        ON CONFLICT(account_name) DO UPDATE SET status='PENDING', last_export_date=?
    """, (account_name, today_str, today_str))
    cursor.execute("""
        INSERT INTO audit_log (module_name, status, details)
        VALUES (?, 'EXPORT_SCHEDULED', ?)
    """, (account_name, f"Scraping failed, scheduled manual export for {account_name}"))
    conn.commit()

if __name__ == "__main__":
    init_db()
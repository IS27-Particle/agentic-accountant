"""
Comprehensive unit and integration test suite for agentic-accountant.
Covers Double-Entry Balancing, Transaction Ingestion, Classification,
Financial Reporting (Balance Sheet, P&L, Cash Flow), and Reconciliation Self-Healing.
"""
import os
import pytest
import sqlite3
import datetime
import database
import llm_router
from accounting_engine import DoubleEntryLedger, TransactionClassifier, FinancialReportGenerator, ReconciliationEngine

@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_state.db")
    os.environ["DB_PATH"] = db_file
    database.DB_PATH = db_file
    conn = sqlite3.connect(db_file)
    database.init_db(conn)
    yield conn
    conn.close()

def test_database_initialization(test_db):
    cursor = test_db.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    assert "raw_transactions" in tables
    assert "chart_of_accounts" in tables
    assert "double_entry_ledger" in tables
    assert "financial_reports" in tables

def test_double_entry_book_balancing(test_db):
    ledger = DoubleEntryLedger(test_db)
    
    # Post salary deposit: Debit Checking (1000), Credit Revenue (4000)
    entry1 = ledger.post_journal_entry(
        date="2026-08-01",
        description="Payroll Deposit",
        debit_account="1000",
        credit_account="4000",
        amount=3000.00
    )
    assert entry1.startswith("je_")
    
    # Post grocery expense: Debit Groceries (5000), Credit Checking (1000)
    entry2 = ledger.post_journal_entry(
        date="2026-08-02",
        description="Grocery Store",
        debit_account="5000",
        credit_account="1000",
        amount=150.00
    )
    assert entry2.startswith("je_")
    
    # Verify Trial Balance
    trial = ledger.get_trial_balance()
    assert trial["balanced"] is True
    assert trial["discrepancy"] == 0.0
    assert trial["total_debits"] == trial["total_credits"]

def test_transaction_classification_rules(test_db):
    classifier = TransactionClassifier(test_db)
    
    debit, credit, conf = classifier.classify("ACME Payroll Corp", 2500.0, "Checking")
    assert debit == "1000"
    assert credit == "4000"
    assert conf >= 0.9
    
    debit, credit, conf = classifier.classify("Whole Foods Market", 85.50, "Credit Card")
    assert debit == "5000"
    assert credit == "2000"

def test_automated_ingestion_and_self_healing(test_db):
    cursor = test_db.cursor()
    cursor.execute("""
        INSERT INTO raw_transactions (id, date, payee, amount, category, account, writeback_status, cleared)
        VALUES ('tx_1', '2026-08-05', 'Electric Utility Co', 120.00, 'Utilities', 'Checking', 'PENDING', 'CLEARED'),
               ('tx_2', '2026-08-06', 'Safeway Groceries', 65.00, 'Food', 'Credit Card', 'PENDING', 'CLEARED')
    """)
    test_db.commit()
    
    recon = ReconciliationEngine(test_db)
    result = recon.run_self_healing_routine()
    
    assert result["status"] == "BALANCED"
    assert result["healed_count"] == 2
    assert result["trial_balance"]["balanced"] is True
    
    # Verify writeback status updated
    cursor.execute("SELECT writeback_status FROM raw_transactions WHERE id='tx_1'")
    assert cursor.fetchone()[0] == "POSTED"

def test_financial_reporting_generators(test_db):
    ledger = DoubleEntryLedger(test_db)
    ledger.post_journal_entry("2026-08-01", "Client Payment", "1000", "4000", 5000.00)
    ledger.post_journal_entry("2026-08-02", "Office Supplies", "5030", "1000", 200.00)
    ledger.post_journal_entry("2026-08-03", "Medical Expense", "5020", "1020", 300.00)
    
    reporter = FinancialReportGenerator(test_db)
    
    # 1. Income Statement (P&L)
    pnl = reporter.generate_income_statement()
    assert pnl["total_revenue"] == 5000.00
    assert pnl["total_expenses"] == 500.00
    assert pnl["net_income"] == 4500.00
    
    # 2. Balance Sheet
    bs = reporter.generate_balance_sheet()
    assert bs["balanced"] is True
    assert bs["total_assets"] == bs["total_liabilities_and_equity"]
    
    # 3. Cash Flow Statement
    cf = reporter.generate_cash_flow_statement()
    assert "operating_activities" in cf
    assert cf["operating_activities"]["net_income"] == 4500.00

def test_llm_router_fallback(test_db):
    model = llm_router.get_model()
    assert model in ["qwen2.5-coder:14b", "qwen2.5-coder:7b"]
    
    # Test router fallback without network error
    text = llm_router.generate_text("Test prompt", temperature=0.1)
    assert isinstance(text, str)

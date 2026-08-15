import hashlib
import json
import time
import os
import re
import sys
import shutil
import sqlite3
import traceback
import llm_router
from playwright.sync_api import sync_playwright

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
                # Strip quotes if present
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                elif v.startswith("'") and v.endswith("'"):
                    v = v[1:-1]
                os.environ[k] = v

load_dotenv()

# --- Logging Output Redirection (Tee to log file) ---
class TeeLogger:
    def __init__(self, filepath):
        self.terminal = sys.stdout
        self.log = open(filepath, "a", encoding="utf-8")

    def write(self, message):
        try:
            self.terminal.write(message)
        except Exception:
            try:
                enc = self.terminal.encoding or 'utf-8'
                self.terminal.write(message.encode(enc, errors='replace').decode(enc))
            except Exception:
                pass
        try:
            self.log.write(message)
            self.log.flush()
        except Exception:
            pass

    def flush(self):
        self.terminal.flush()
        self.log.flush()

# Set up logging to orchestrator.log
LOG_PATH = "orchestrator.log"
if os.path.exists("/workspace"):
    LOG_PATH = "/workspace/orchestrator.log"
sys.stdout = TeeLogger(LOG_PATH)
sys.stderr = sys.stdout

import database
from interpreter import UniversalInterfaceEngine

# --- Configuration ---
# DB_PATH and CONFIG_PATH resolution to support host and docker execution
DB_PATH = database.DB_PATH

CONFIG_PATH = os.environ.get("CONFIG_PATH")
if not CONFIG_PATH:
    if os.path.exists("/workspace") or os.name != 'nt':
        CONFIG_PATH = "/workspace/modules_config.json"
    else:
        CONFIG_PATH = "modules_config.json"

MODEL_ORCHESTRATOR = "gemini-1.5-pro"
MODEL_ROUTINE = "gemini-1.5-flash"

# --- AI Infrastructure ---
_cache = {}

def get_model(task_type="routine"):
    return MODEL_ORCHESTRATOR if task_type == "orchestration" else MODEL_ROUTINE

def get_cached_response(input_data):
    input_hash = hashlib.sha256(json.dumps(input_data, sort_keys=True).encode()).hexdigest()
    if input_hash in _cache and (time.time() - _cache[input_hash]['timestamp'] < 86400):
        return _cache[input_hash]['response']
    return None

def set_cached_response(input_data, response):
    input_hash = hashlib.sha256(json.dumps(input_data, sort_keys=True).encode()).hexdigest()
    _cache[input_hash] = {'response': response, 'timestamp': time.time()}

def strip_dom(html_content):
    # Strip script, style, svg, and noscript with their closing tags
    html_content = re.sub(r'<(script|style|svg|noscript)[^>]*>.*?</\1>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    # Strip img tags which might not have a closing </img> tag
    html_content = re.sub(r'<img[^>]*>(</img>)?', '', html_content, flags=re.IGNORECASE)
    return html_content

def batch_categorize_transactions(transactions):
    if not transactions:
        return []
    cached = get_cached_response(transactions)
    if cached:
        return cached
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY environment variable not set. Using local mock categorization.")
        # Fallback dictionary matching payee substrings
        fallback_categories = {
            "amazon": "Shopping",
            "target": "Groceries",
            "simplifi": "Financial",
            "forma": "Health & HSA",
            "medical": "Medical Services"
        }
        categorized = []
        for tx in transactions:
            payee_lower = str(tx.get("payee", "")).lower()
            matched_cat = "Uncategorized"
            for kw, cat in fallback_categories.items():
                if kw in payee_lower:
                    matched_cat = cat
                    break
            categorized.append({"id": tx.get("id"), "category": matched_cat})
        return categorized
        
    # Connected via local llm_router
    prompt = f"Categorize these {len(transactions)} transactions into standard financial categories: {json.dumps(transactions)}. Return a JSON array of objects with 'id' and 'category' keys."
    
    response = llm_router.generate_text(prompt)
    try:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        parsed = json.loads(cleaned)
        set_cached_response(transactions, parsed)
        return parsed
    except Exception as parse_err:
        print(f"Failed to parse Gemini categorization response: {parse_err}. Response text: {response}")
        fallback_categories = {
            "amazon": "Shopping",
            "target": "Groceries",
            "simplifi": "Financial",
            "forma": "Health & HSA",
            "medical": "Medical Services"
        }
        categorized = []
        for tx in transactions:
            payee_lower = str(tx.get("payee", "")).lower()
            matched_cat = "Uncategorized"
            for kw, cat in fallback_categories.items():
                if kw in payee_lower:
                    matched_cat = cat
                    break
            categorized.append({"id": tx.get("id"), "category": matched_cat})
        return categorized

def attempt_self_healing(node, error_traceback):
    print(f"Self-healing triggered for node: {node}")
    
    # 1. Selector Safeguard: check for network drop / name resolution errors
    network_errors = [
        "net::ERR_NAME_NOT_RESOLVED", 
        "net::ERR_CONNECTION_REFUSED", 
        "net::ERR_CONNECTION_TIMED_OUT", 
        "net::ERR_INTERNET_DISCONNECTED",
        "net::ERR_ADDRESS_UNREACHABLE",
        "net::ERR_PROXY_CONNECTION_FAILED"
    ]
    for err in network_errors:
        if err in error_traceback:
            print(f"Self-healing bypassed for node {node}: temporary network error detected ({err}).")
            return False
            
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set. Cannot run AI-driven self-healing.")
        return False
        
    try:
        # Read the current config file
        with open(CONFIG_PATH, "r") as f:
            config_text = f.read()
            
        # Read the current interpreter file
        interpreter_path = "interpreter.py"
        if os.path.exists("/workspace/interpreter.py"):
            interpreter_path = "/workspace/interpreter.py"
        with open(interpreter_path, "r", encoding="utf-8") as f:
            interpreter_code = f.read()
            
        # Connected via local llm_router
        prompt = f"""
You are the self-healing engine of the Declarative Agentic Accountant.
An error has occurred during the execution of module '{node}'. This could be caused by:
1. A layout change requiring selector/parameter modifications in modules_config.json.
2. A logic or parsing error inside interpreter.py.

Failed Module Name: {node}
Error Traceback:
{error_traceback}

Current modules_config.json:
{config_text}

Current interpreter.py:
{interpreter_code}

Diagnose the failure. Correct either the configuration parameters in modules_config.json OR the Python code logic in interpreter.py (or both if necessary).
Return a JSON object with two keys:
1. "repaired_config": The complete, valid JSON configuration of modules_config.json. If no config change is needed, set this to null.
2. "repaired_interpreter": The complete, valid Python code of interpreter.py. If no code change is needed, set this to null.

Do NOT include markdown code blocks, explanations, or wrappers. Output ONLY the raw JSON object.
"""
        response = client.models.generate_content(
            model=MODEL_ROUTINE,
            contents=prompt
        )
        
        repaired_json_str = response.text.strip()
        # Strip potential markdown wrapper backticks
        if repaired_json_str.startswith("```"):
            lines = repaired_json_str.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            repaired_json_str = "\n".join(lines).strip()
        
        # Validate JSON
        parsed_heal = json.loads(repaired_json_str)
        repaired_config = parsed_heal.get("repaired_config")
        repaired_interpreter = parsed_heal.get("repaired_interpreter")
        
        if not repaired_config and not repaired_interpreter:
            print("Self-healing: No changes proposed by Gemini.")
            return False
            
        if repaired_config:
            # Create backup of config before writing
            backup_conf_path = CONFIG_PATH + ".bak"
            try:
                shutil.copy(CONFIG_PATH, backup_conf_path)
                print(f"Self-healing: Created configuration backup at {backup_conf_path}")
            except Exception as backup_err:
                print(f"Self-healing warning: failed to create config backup: {backup_err}")
            
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(repaired_config, f, indent=2)
            print("Self-healing: Saved healed modules_config.json to disk.")
            
        if repaired_interpreter:
            # Create backup of interpreter before writing
            backup_int_path = interpreter_path + ".bak"
            try:
                shutil.copy(interpreter_path, backup_int_path)
                print(f"Self-healing: Created interpreter code backup at {backup_int_path}")
            except Exception as backup_err:
                print(f"Self-healing warning: failed to create interpreter backup: {backup_err}")
                
            with open(interpreter_path, "w", encoding="utf-8") as f:
                f.write(repaired_interpreter)
            print("Self-healing: Saved healed interpreter.py to disk.")
        
        # Log self-healing log to DB
        conn_db = sqlite3.connect(DB_PATH)
        database.log_audit(conn_db, node, "SELF_HEALED", f"Successfully ran self-healing logic. Config updated: {repaired_config is not None}, Interpreter updated: {repaired_interpreter is not None}")
        # Log reasoning to ai_reasoning_log
        cursor = conn_db.cursor()
        cursor.execute("INSERT INTO ai_reasoning_log (prompt, response, timestamp) VALUES (?, ?, CURRENT_TIMESTAMP)", (prompt, repaired_json_str))
        conn_db.commit()
        conn_db.close()
        
        return True
    except Exception as sh_err:
        print(f"Self-healing failed: {sh_err}")
        return False

# --- Core Pipeline ---
def run_pipeline():
    conn = sqlite3.connect(DB_PATH)
    database.init_db(conn)
    engine = UniversalInterfaceEngine(CONFIG_PATH)
    
    # Dynamically retrieve execution order from active budget_sites
    cursor = conn.cursor()
    cursor.execute("SELECT site_name FROM budget_sites WHERE status = 'ACTIVE'")
    active_rows = cursor.fetchall()
    if active_rows:
        order = [row[0] for row in active_rows]
    else:
        order = engine.config.get("execution_order", [])
    
    with sync_playwright() as p:
        is_headless = os.getenv("HEADLESS", "0") == "1" or (not os.getenv("DISPLAY") and os.name != 'nt')
        
        browser = p.chromium.launch_persistent_context(
            user_data_dir="/workspace/user_session_data" if os.path.exists("/workspace") else "user_session_data",
            headless=is_headless,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            args=["--no-sandbox", "--disable-setuid-sandbox", "--remote-debugging-port=6080"]
        )
        
        for node in order:
            print(f"--- Processing: {node} ---")
            
            # 1. Simplifi Check
            if not database.is_connected_in_simplifi(conn, node):
                database.flag_for_simplifi_connection(conn, node)
                continue
                
            # 2. Try Web Scraping
            try:
                engine.execute_node(node, browser, conn)
                database.log_audit(conn, node, "SUCCESS")
            except Exception as e:
                tb_str = traceback.format_exc()
                print(f"Scraping failed for {node}: {e}. Attempting self-healing.")
                
                # Log first failure
                database.log_audit(conn, node, "FAILED_SCRAPING", tb_str)
                
                # Attempt self-healing once
                healed = attempt_self_healing(node, tb_str)
                
                if healed:
                    # Re-initialize engine and re-execute
                    try:
                        print(f"Re-executing healed module {node}")
                        healed_engine = UniversalInterfaceEngine(CONFIG_PATH)
                        healed_engine.execute_node(node, browser, conn)
                        database.log_audit(conn, node, "SUCCESS_AFTER_HEALING")
                        continue
                    except Exception as retry_err:
                        print(f"Re-execution failed: {retry_err}. Rolling back configuration and code.")
                        database.log_audit(conn, node, "FAILED_AFTER_HEALING", traceback.format_exc())
                        # Rollback modules_config.json from backup
                        backup_path = CONFIG_PATH + ".bak"
                        if os.path.exists(backup_path):
                            try:
                                shutil.copy(backup_path, CONFIG_PATH)
                                print("Self-healing rollback of modules_config.json completed successfully.")
                            except Exception as rb_err:
                                print(f"Self-healing rollback of modules_config.json failed: {rb_err}")
                        
                        # Rollback interpreter.py from backup
                        interpreter_path = "interpreter.py"
                        if os.path.exists("/workspace/interpreter.py"):
                            interpreter_path = "/workspace/interpreter.py"
                        backup_int_path = interpreter_path + ".bak"
                        if os.path.exists(backup_int_path):
                            try:
                                shutil.copy(backup_int_path, interpreter_path)
                                print("Self-healing rollback of interpreter.py completed successfully.")
                            except Exception as rb_err:
                                print(f"Self-healing rollback of interpreter.py failed: {rb_err}")
                
                print(f"Scheduling manual export for {node}.")
                database.flag_for_manual_export(conn, node)
        
        browser.close()
        
    try:
        print("Running transaction validation pipeline...")
        import validation
        validation.run_validation_pipeline(conn)
    except Exception as v_err:
        print(f"Error running transaction validation pipeline: {v_err}")
        traceback.print_exc()
        
    conn.close()

if __name__ == "__main__":
    run_pipeline()
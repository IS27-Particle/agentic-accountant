"""
Local LLM Router for agentic-accountant.
Routes inference calls to local Ollama instance (http://10.0.0.25:11434, qwen2.5-coder:14b)
with configurable fallback options via environment variables.
"""
import os
import json
import requests
import re
from typing import Dict, Any, Optional, List

OLLAMA_HOST = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or "http://10.0.0.25:11434"
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL") or "qwen2.5-coder:14b"
ROUTINE_MODEL = os.environ.get("OLLAMA_ROUTINE_MODEL") or DEFAULT_MODEL
COMPLEX_MODEL = os.environ.get("OLLAMA_COMPLEX_MODEL") or DEFAULT_MODEL

def get_model(purpose: str = "routine") -> str:
    """Return model identifier based on task complexity."""
    if purpose == "complex":
        return COMPLEX_MODEL
    return ROUTINE_MODEL

def generate_text(prompt: str, model: Optional[str] = None, system: Optional[str] = None, temperature: float = 0.2) -> str:
    """Generate text completion using local Ollama instance."""
    target_model = model or get_model()
    url = f"{OLLAMA_HOST.rstrip('/')}/api/generate"
    payload = {
        "model": target_model,
        "prompt": prompt,
        "system": system or "You are an expert autonomous accounting AI assistant.",
        "stream": False,
        "options": {
            "temperature": temperature
        }
    }
    try:
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"[LLM Router Warning] Local Ollama request failed: {e}. Falling back to rule engine.")
    return ""

def generate_json(prompt: str, model: Optional[str] = None, system: Optional[str] = None) -> Dict[str, Any]:
    """Generate structured JSON response from local Ollama instance."""
    sys_prompt = (system or "You are an expert accounting AI.") + " Respond ONLY with a valid JSON object."
    text = generate_text(prompt, model=model, system=sys_prompt)
    if not text:
        return {}
    
    # Try markdown json block
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        try:
            return json.loads(candidate)
        except Exception:
            pass
            
    # Try raw JSON block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidate = match.group(0).strip()
        try:
            return json.loads(candidate)
        except Exception:
            pass
            
    try:
        return json.loads(text)
    except Exception:
        return {}

def chat(messages: List[Dict[str, str]], model: Optional[str] = None) -> str:
    """Chat completion endpoint."""
    target_model = model or get_model()
    url = f"{OLLAMA_HOST.rstrip('/')}/api/chat"
    payload = {
        "model": target_model,
        "messages": messages,
        "stream": False
    }
    try:
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code == 200:
            msg = resp.json().get("message", {})
            return msg.get("content", "").strip()
    except Exception as e:
        print(f"[LLM Router Warning] Chat request failed: {e}")
    return ""

"""
Script de test rapide des endpoints
Lancer avec : python test_endpoints.py
(Le serveur doit être démarré)
"""

import httpx
import json

BASE = "http://localhost:8000"

def test_orchestrator():
    print("\n=== Test Orchestrateur ===")
    payload = {
        "request_id": "test-123",
        "user_email": "test@example.com",
        "user_id": "user-1",
        "user_name": "Anthony",
        "instruction": "Envoie un mail à Martin pour reporter le rendez-vous de mardi",
        "conversation_history": "",
        "last_agent": ""
    }
    r = httpx.post(f"{BASE}/orchestrator", json=payload, timeout=30)
    print("Status:", r.status_code)
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))


def test_mail_agent():
    print("\n=== Test Agent Mail ===")
    payload = {
        "user_id": "user-1",
        "instruction": "Envoie un mail à Martin pour reporter le rendez-vous de mardi matin",
        "request_id": "test-456"
    }
    r = httpx.post(f"{BASE}/agent/mail", json=payload, timeout=30)
    print("Status:", r.status_code)
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    test_orchestrator()
    test_mail_agent()

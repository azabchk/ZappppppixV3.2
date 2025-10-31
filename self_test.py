#!/usr/bin/env python3
"""Basic integration test for ZappppppixV3.2.

This script exercises a number of API flows: user registration, deposit,
withdraw, order placement, order matching and cancellation.  It is not
exhaustive but provides confidence that core functionality works.  The script
expects the API to be running on http://localhost:8000 and will exit with
status code 0 on success or a non‑zero code on failure.

Usage:
    python self_test.py
"""

import os
import sys
import time
import uuid

import httpx


API_URL = os.getenv("API_URL", "http://localhost:8000")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme-admin-token")


def wait_for_service(url: str, timeout: float = 30.0) -> None:
    """Wait until the API responds to /health or raise TimeoutError."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/health", timeout=5.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise TimeoutError("Service did not respond within timeout")


def main() -> int:
    client = httpx.Client()
    # Wait for the service to be ready
    print("Waiting for API to become available...")
    wait_for_service(API_URL)
    # Register two users
    print("Registering two users...")
    res = client.post(f"{API_URL}/api/v1/public/register", json={"name": "Alice"})
    assert res.status_code == 200, res.text
    user_a = res.json()
    res = client.post(f"{API_URL}/api/v1/public/register", json={"name": "Bob"})
    assert res.status_code == 200, res.text
    user_b = res.json()
    # Add a test instrument
    print("Adding test instrument TEST...")
    admin_headers = {"Authorization": f"TOKEN {ADMIN_TOKEN}"}
    res = client.post(
        f"{API_URL}/api/v1/admin/instrument",
        headers=admin_headers,
        json={"ticker": "TEST", "type": "STOCK"},
    )
    assert res.status_code in (200, 201), res.text
    # Deposit balances
    print("Depositing balances...")
    def deposit(user_id: str, ticker: str, amount: int) -> None:
        r = client.post(
            f"{API_URL}/api/v1/admin/balance/deposit",
            headers=admin_headers,
            json={"user_id": user_id, "ticker": ticker, "amount": amount},
        )
        assert r.status_code == 200, r.text

    deposit(user_a["id"], "RUB", 1000)
    deposit(user_b["id"], "RUB", 1000)
    deposit(user_a["id"], "TEST", 100)

    # User A places a limit sell order for 10 TEST at price 10
    print("User A placing limit sell order...")
    headers_a = {"Authorization": f"TOKEN {user_a['api_key']}"}
    res = client.post(
        f"{API_URL}/api/v1/order",
        headers=headers_a,
        json={"ticker": "TEST", "side": "SELL", "type": "LIMIT", "quantity": 10, "price": 10},
    )
    assert res.status_code == 200, res.text
    order_a = res.json()
    # User B places a market buy order for 10 TEST
    print("User B placing market buy order...")
    headers_b = {"Authorization": f"TOKEN {user_b['api_key']}"}
    res = client.post(
        f"{API_URL}/api/v1/order",
        headers=headers_b,
        json={"ticker": "TEST", "side": "BUY", "type": "MARKET", "quantity": 10},
    )
    assert res.status_code == 200, res.text
    order_b = res.json()
    # Verify balances after trade
    print("Checking balances after trade...")
    bal_a = client.get(f"{API_URL}/api/v1/balance", headers=headers_a).json()
    bal_b = client.get(f"{API_URL}/api/v1/balance", headers=headers_b).json()
    assert bal_a.get("TEST") == 90, f"Expected Alice TEST balance 90, got {bal_a.get('TEST')}"
    assert bal_a.get("RUB") == 1100, f"Expected Alice RUB balance 1100, got {bal_a.get('RUB')}"
    assert bal_b.get("TEST") == 10, f"Expected Bob TEST balance 10, got {bal_b.get('TEST')}"
    assert bal_b.get("RUB") == 900, f"Expected Bob RUB balance 900, got {bal_b.get('RUB')}"
    # Verify order statuses
    print("Checking order statuses...")
    res_a = client.get(f"{API_URL}/api/v1/order/{order_a['order_id']}", headers=headers_a).json()
    res_b = client.get(f"{API_URL}/api/v1/order/{order_b['order_id']}", headers=headers_b).json()
    assert res_a["status"] == "EXECUTED", f"Alice order status {res_a['status']}"
    assert res_b["status"] == "EXECUTED", f"Bob order status {res_b['status']}"
    # Verify orderbook empty for TEST
    print("Checking order book...")
    ob = client.get(f"{API_URL}/api/v1/public/orderbook/TEST").json()
    assert ob["bids"] == [] and ob["asks"] == [], f"Order book not empty: {ob}"
    # Verify transactions include our trade
    print("Checking transactions...")
    txs = client.get(f"{API_URL}/api/v1/public/transactions/TEST").json()
    assert len(txs) >= 1, "No transactions recorded"
    # Attempt to cancel executed order
    print("Attempting to cancel executed order (should fail)...")
    r = client.delete(f"{API_URL}/api/v1/order/{order_a['order_id']}", headers=headers_a)
    assert r.status_code in (400, 409), f"Unexpected status code: {r.status_code}"
    print("All checks passed!")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Test failed: {exc}")
        sys.exit(1)
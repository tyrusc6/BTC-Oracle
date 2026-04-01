"""
BTC Oracle - Supabase Helper (Production Grade)
"""

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

TIMEOUT = 15
MAX_RETRIES = 2


def _request(method, url, json_data=None, retries=MAX_RETRIES):
    """HTTP request with retry logic and timeout."""
    for attempt in range(retries + 1):
        try:
            if method == "GET":
                resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            elif method == "POST":
                resp = requests.post(url, json=json_data, headers=HEADERS, timeout=TIMEOUT)
            elif method == "PATCH":
                resp = requests.patch(url, json=json_data, headers=HEADERS, timeout=TIMEOUT)
            return resp
        except requests.exceptions.Timeout:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"Request timeout after {retries + 1} attempts: {url}")
            return None
        except requests.exceptions.ConnectionError:
            if attempt < retries:
                time.sleep(2)
                continue
            print(f"Connection error after {retries + 1} attempts: {url}")
            return None
        except Exception as e:
            print(f"Request error: {e}")
            return None
    return None


def insert(table, data):
    """Insert a single row."""
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    resp = _request("POST", url, data)
    if resp and resp.status_code in (200, 201):
        try:
            return resp.json()
        except:
            return True
    elif resp:
        print(f"Insert error ({table}): {resp.status_code} - {resp.text[:200]}")
    return None


def batch_insert(table, rows):
    """Insert multiple rows in one request."""
    if not rows:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    resp = _request("POST", url, rows)
    if resp and resp.status_code in (200, 201):
        try:
            return resp.json()
        except:
            return True
    elif resp:
        print(f"Batch insert error ({table}): {resp.status_code} - {resp.text[:200]}")
    return None


def select(table, params=""):
    """Select rows. NOTE: Supabase default limit is 1000 rows."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
    resp = _request("GET", url)
    if resp and resp.status_code == 200:
        try:
            return resp.json()
        except:
            return []
    elif resp:
        print(f"Select error ({table}): {resp.status_code} - {resp.text[:200]}")
    return []


def count(table, params=""):
    """Count rows matching params without downloading all data.
    Uses Supabase exact count via HEAD + Prefer header."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}&select=id"
    count_headers = {**HEADERS, "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"}
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=count_headers, timeout=TIMEOUT)
            if resp.status_code in (200, 206):
                content_range = resp.headers.get("Content-Range", "")
                # Format: "0-0/1234" or "*/1234"
                if "/" in content_range:
                    total = content_range.split("/")[-1]
                    if total != "*":
                        return int(total)
            return 0
        except:
            if attempt < MAX_RETRIES:
                time.sleep(1)
                continue
    return 0


def count_where(table, outcome=None):
    """Count wins/losses/total efficiently."""
    if outcome:
        return count(table, f"outcome=eq.{outcome}")
    return count(table, "outcome=not.is.null")


def update(table, match_column, match_value, data):
    """Update rows matching a condition."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{match_column}=eq.{match_value}"
    resp = _request("PATCH", url, data)
    if resp and resp.status_code in (200, 204):
        try:
            return resp.json() if resp.text.strip() else True
        except:
            return True
    elif resp:
        print(f"Update error ({table}): {resp.status_code} - {resp.text[:200]}")
    return None

"""Create and send one test invoice in the PayPal sandbox.

Setup:
    pip install requests python-dotenv
    .env file (same folder, never commit it):
        PAYPAL_CLIENT_ID=...
        PAYPAL_SECRET=...
        PAYER_EMAIL=<email of your sandbox PERSONAL account>
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
BASE = "https://api-m.sandbox.paypal.com"
CLIENT_ID = os.environ["PAYPAL_CLIENT_ID"]
SECRET = os.environ["PAYPAL_SECRET"]
PAYER_EMAIL = os.environ["PAYER_EMAIL"]


def check(resp, step):
    if not resp.ok:
        print(f"{step} failed: {resp.status_code}\n{resp.text}")
        sys.exit(1)
    return resp


def get_token():
    r = requests.post(
        f"{BASE}/v1/oauth2/token",
        auth=(CLIENT_ID, SECRET),
        data={"grant_type": "client_credentials"},
    )
    data = check(r, "Auth").json()
    scope = data.get("scope", "")
    print("Invoicing scope granted:", "services/invoicing" in scope)
    return data["access_token"]


def create_draft(headers):
    payload = {
        "detail": {"currency_code": "USD"},
        "primary_recipients": [
            {"billing_info": {"email_address": PAYER_EMAIL}}
        ],
        "items": [
            {
                "name": "Test service",
                "quantity": "1",
                "unit_amount": {"currency_code": "USD", "value": "10.00"},
            }
        ],
    }
    invoicer = os.getenv("INVOICER_EMAIL")
    if invoicer:
        payload["invoicer"] = {"email_address": invoicer}
    r = requests.post(f"{BASE}/v2/invoicing/invoices", json=payload, headers=headers)
    href = check(r, "Create draft").json()["href"]
    return href.rsplit("/", 1)[-1]


def send_invoice(headers, invoice_id):
    r = requests.post(
        f"{BASE}/v2/invoicing/invoices/{invoice_id}/send",
        json={"send_to_recipient": True},
        headers=headers,
    )
    return check(r, "Send").json()


if __name__ == "__main__":
    headers = {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}
    invoice_id = create_draft(headers)
    print("Draft created:", invoice_id)
    print("Sent:", send_invoice(headers, invoice_id))
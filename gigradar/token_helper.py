"""One-time helper to turn the short-lived token from Graph API Explorer into
a long-lived one, and find your Instagram user ID.

  python -m gigradar.token_helper --app-id 123 --app-secret abc --token SHORT_LIVED_TOKEN

Prints:
  - your long-lived user token (~60 days)
  - a Page token for each Page you manage (made from a long-lived user token,
    these normally don't expire - this is the one to save as IG_ACCESS_TOKEN)
  - the Instagram professional account linked to each Page (IG_USER_ID)
"""
import argparse

import requests

from .instagram import GRAPH


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-id", required=True)
    ap.add_argument("--app-secret", required=True)
    ap.add_argument("--token", required=True, help="short-lived user token from Graph API Explorer")
    a = ap.parse_args()

    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": a.app_id,
        "client_secret": a.app_secret, "fb_exchange_token": a.token,
    }, timeout=30).json()
    if "error" in r:
        raise SystemExit(f"Token exchange failed: {r['error'].get('message')}")
    long_user = r["access_token"]
    print("Long-lived user token (expires in ~60 days):\n ", long_user, "\n")

    pages = requests.get(f"{GRAPH}/me/accounts", params={"access_token": long_user}, timeout=30).json()
    if "error" in pages:
        raise SystemExit(f"Couldn't list Pages: {pages['error'].get('message')}")
    if not pages.get("data"):
        raise SystemExit("No Facebook Pages found. Link your Instagram account to a Facebook Page first.")

    for p in pages["data"]:
        ig = requests.get(f"{GRAPH}/{p['id']}", params={
            "fields": "instagram_business_account{id,username}", "access_token": p["access_token"],
        }, timeout=30).json().get("instagram_business_account")
        print(f"Page: {p['name']} ({p['id']})")
        print(f"  Page token (save as IG_ACCESS_TOKEN): {p['access_token']}")
        if ig:
            print(f"  Instagram: @{ig.get('username')}  ->  IG_USER_ID = {ig['id']}")
        else:
            print("  No Instagram professional account linked to this Page.")
        print()


if __name__ == "__main__":
    main()

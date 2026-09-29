"""How does the live API accept tag writes, and where do they show up?"""
from __future__ import annotations

import json
import pathlib

import httpx

ROOT = next((p for p in pathlib.Path(__file__).resolve().parents if (p / ".elab_key").exists()),
            pathlib.Path(__file__).resolve().parents[2])
BASE = "https://elntest.ub.tum.de/api/v2"
KEY = (ROOT / ".elab_key").read_text(encoding="utf-8").strip()
H = {"Authorization": KEY}

with httpx.Client(base_url=BASE, headers=H, timeout=30.0) as c:
    for row in c.get("/experiments", params={"q": "elabmcp-tag-probe", "limit": 10}).json() or []:
        c.delete(f"/experiments/{row['id']}")
    exp = int(c.post("/experiments", json={"title": "elabmcp-tag-probe"}).headers["location"].rstrip("/").split("/")[-1])

    def state(label: str) -> None:
        payload = c.get(f"/experiments/{exp}").json()
        sub = c.get(f"/experiments/{exp}/tags").json()
        print(f"  {label}: entity tags = {json.dumps(payload.get('tags'))[:80]} | tags endpoint = {json.dumps(sub)[:100]}")

    state("fresh")
    print("\n1) POST {'tags': ['a1', 'b1']}:",
          c.post(f"/experiments/{exp}/tags", json={"tags": ["a1", "b1"]}).status_code)
    state("after tags[]")
    print("2) POST {'tag': 'a2'}:", c.post(f"/experiments/{exp}/tags", json={"tag": "a2"}).status_code)
    state("after tag")
    print("3) POST {'tags': ['a1']} (duplicate):",
          c.post(f"/experiments/{exp}/tags", json={"tags": ["a1"]}).status_code)
    state("after duplicate")

    print("\ncleanup:", c.delete(f"/experiments/{exp}").status_code)

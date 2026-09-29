"""Which metadata payload shape does eLabFTW 6.0.1 accept on PATCH?"""
from __future__ import annotations

import json
import pathlib

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = "https://elntest.ub.tum.de/api/v2"
KEY = (ROOT / ".elab_key").read_text(encoding="utf-8").strip()
H = {"Authorization": KEY}
META = {"elabftw": {"extra_fields_groups": [{"id": 1, "name": "Probe"}]},
        "extra_fields": {"Probe field": {"type": "text", "value": "v1", "group_id": 1,
                                         "allow_multi_values": False}}}

with httpx.Client(base_url=BASE, headers=H, timeout=30.0) as c:
    for row in c.get("/experiments", params={"q": "elabmcp-meta-probe", "limit": 10}).json() or []:
        c.delete(f"/experiments/{row['id']}")
    exp = int(c.post("/experiments", json={"title": "elabmcp-meta-probe"}).headers["location"].rstrip("/").split("/")[-1])
    print("experiment:", exp)

    print("\n1) metadata as JSON string:")
    r = c.patch(f"/experiments/{exp}", json={"metadata": json.dumps(META)})
    print(f"   HTTP {r.status_code} | {r.text[:180]}")
    if r.status_code in (200, 204):
        back = c.get(f"/experiments/{exp}").json().get("metadata")
        back = json.loads(back) if isinstance(back, str) else back
        print("   read back:", json.dumps(back)[:200])

    print("\n2) metadata as object:")
    r = c.patch(f"/experiments/{exp}", json={"metadata": META})
    print(f"   HTTP {r.status_code} | {r.text[:180]}")

    print("\n3) string + metadatamerge on an existing field:")
    r = c.patch(f"/experiments/{exp}", json={"metadata": json.dumps(
        {"extra_fields": {"Probe field": {"type": "text", "value": "v2", "group_id": 1}}})})
    print(f"   HTTP {r.status_code} | {r.text[:150]}")
    back = c.get(f"/experiments/{exp}").json().get("metadata")
    back = json.loads(back) if isinstance(back, str) else back
    print("   read back:", json.dumps(back)[:220])

    print("\ncleanup:", c.delete(f"/experiments/{exp}").status_code)

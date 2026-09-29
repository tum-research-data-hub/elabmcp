"""Which step create/update parameters does eLabFTW 6.0.1 actually accept?"""
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
    for row in c.get("/experiments", params={"q": "elabmcp-step-probe2", "limit": 10}).json() or []:
        c.delete(f"/experiments/{row['id']}")
    exp = int(c.post("/experiments", json={"title": "elabmcp-step-probe2"}).headers["location"].rstrip("/").split("/")[-1])

    print("=== POST /steps variants ===")
    for payload in ({"body": "s1"}, {"body": "s2", "deadline": "2027-05-01"},
                    {"body": "s3", "deadline": "2027-05-01", "deadline_notif": 1},
                    {"body": "s4", "deadline_notif": 1}):
        r = c.post(f"/experiments/{exp}/steps", json=payload)
        sid = r.headers.get("location", "").rstrip("/").split("/")[-1] if r.status_code == 201 else None
        got = None
        if sid:
            steps = c.get(f"/experiments/{exp}/steps").json()
            got = next((s for s in steps if str(s["id"]) == sid), None)
        print(f"  POST {json.dumps(payload)}: HTTP {r.status_code} | stored: "
              f"{json.dumps({k: got.get(k) for k in ('deadline', 'deadline_notif', 'body')}) if got else r.text[:90]}")

    steps = c.get(f"/experiments/{exp}/steps").json()
    sid = steps[0]["id"]
    print(f"\n=== PATCH /steps/{sid} variants ===")
    for payload in ({"action": "update", "body": "edited"},
                    {"body": "edited2"},
                    {"action": "update"},
                    {"action": "update", "body": "edited3", "deadline": "2027-08-01"},
                    {"action": "update", "body": "edited4", "deadline": "2027-08-01", "deadline_notif": 1},
                    {"action": "finish"},
                    {"action": "finish", "finished": 1},
                    {"action": "finish", "finished": 0}):
        r = c.patch(f"/experiments/{exp}/steps/{sid}", json=payload)
        state = c.get(f"/experiments/{exp}/steps").json()
        cur = next((s for s in state if s["id"] == sid), {})
        print(f"  PATCH {json.dumps(payload)}: HTTP {r.status_code} | body={cur.get('body')!r} "
              f"deadline={cur.get('deadline')!r} notif={cur.get('deadline_notif')} finished={cur.get('finished')} "
              f"{'' if r.status_code < 400 else '| ' + r.text[:80]}")

    print("\ncleanup:", c.delete(f"/experiments/{exp}").status_code)

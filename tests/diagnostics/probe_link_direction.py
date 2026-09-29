"""Determine which endpoint lists which side of a link (direction semantics)."""
from __future__ import annotations

import json
import pathlib

import httpx

ROOT = next((p for p in pathlib.Path(__file__).resolve().parents if (p / ".elab_key").exists()),
            pathlib.Path(__file__).resolve().parents[2])
BASE = "https://elntest.ub.tum.de/api/v2"
KEY = (ROOT / ".elab_key").read_text(encoding="utf-8").strip()
H = {"Authorization": KEY}


def ids(rows) -> list:
    if isinstance(rows, dict):
        rows = [rows]
    return [(r.get("id"), r.get("entityid"), r.get("type")) for r in rows if isinstance(r, dict)]


with httpx.Client(base_url=BASE, headers=H, timeout=30.0) as c:
    for etype in ("experiments", "items"):
        for row in c.get(f"/{etype}", params={"q": "elabmcp-probe", "limit": 20}).json() or []:
            c.delete(f"/{etype}/{row['id']}")

    a = int(c.post("/experiments", json={"title": "elabmcp-probe-dir-A"}).headers["location"].rstrip("/").split("/")[-1])
    b = int(c.post("/items", json={"title": "elabmcp-probe-dir-B"}).headers["location"].rstrip("/").split("/")[-1])
    print(f"A(experiment)={a}  B(item)={b}\n")

    print("1) link  A -- items_links --> B:", c.post(f"/experiments/{a}/items_links/{b}", json={"action": "create"}).status_code)
    print("   GET /experiments/A/items_links  :", ids(c.get(f"/experiments/{a}/items_links").json()))
    print("   GET /items/B/experiments_links  :", ids(c.get(f"/items/{b}/experiments_links").json()))
    print("   GET /items?related=A&origin=exp :", ids(c.get("/items", params={"related": a, "related_origin": "experiments"}).json()))
    print("   GET /experiments?related=B&origin=items:",
          ids(c.get("/experiments", params={"related": b, "related_origin": "items"}).json()))
    print("   DELETE link:", c.delete(f"/experiments/{a}/items_links/{b}").status_code)

    print("\n2) link  A <-- items_links -- B  (created from the item side):",
          c.post(f"/items/{b}/experiments_links/{a}", json={"action": "create"}).status_code)
    print("   GET /experiments/A/items_links  :", ids(c.get(f"/experiments/{a}/items_links").json()))
    print("   GET /items/B/experiments_links  :", ids(c.get(f"/items/{b}/experiments_links").json()))
    print("   GET /items?related=A&origin=exp :", ids(c.get("/items", params={"related": a, "related_origin": "experiments"}).json()))
    print("   GET /experiments?related=B&origin=items:",
          ids(c.get("/experiments", params={"related": b, "related_origin": "items"}).json()))

    print("\n3) cleanup:")
    print("   unlink:", c.delete(f"/items/{b}/experiments_links/{a}").status_code,
          "| item:", c.delete(f"/items/{b}").status_code,
          "| experiment:", c.delete(f"/experiments/{a}").status_code)

#!/usr/bin/env python3
"""Run geo-optimizer audits for every unfoldingWord site and write dashboard data.

Usage:
    python3 geo_audit_all.py                  # audit all sites in sites.txt
    python3 geo_audit_all.py --out results/   # choose output folder

Writes:
    <out>/geo-latest.json   rows for this run only
    <out>/geo-history.json  every run so far (appended; one row per site per run)

Each row is flat so the GEO dashboard can load it directly.
Requires `uv` (for `uvx`). No other dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = os.environ.get("GEO_PKG", "geo-optimizer-skill==4.19.10")  # pinned so scores stay comparable
CATEGORIES = [
    "robots_txt", "llms_txt", "schema_jsonld", "meta_tags", "content",
    "signals", "ai_discovery", "brand_entity", "google_ai",
]
TARGET = 90


def load_sites(path: Path) -> list[str]:
    sites = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            sites.append(line if line.startswith("http") else f"https://{line}")
    return sites


def audit(url: str, geo_cmd: list[str], timeout: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "audit.json"
        cmd = geo_cmd + ["audit", "--url", url, "--format", "json", "--output", str(out)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"error": f"Timed out after {timeout}s"}
        if out.exists():
            try:
                return json.loads(out.read_text())
            except json.JSONDecodeError as e:
                return {"error": f"Bad JSON from geo: {e}"}
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"error": (msg[-1] if msg else f"geo exited {proc.returncode}")[:300]}


def to_row(url: str, run_at: str, data: dict) -> dict:
    host = url.split("://", 1)[-1].rstrip("/")
    score = data.get("score")
    err = data.get("error")
    row = {
        "run_at": run_at,
        "site": host,
        "url": url,
        "score": score if not err else None,
        "band": data.get("band") if not err else None,
        "meets_target": bool(score is not None and not err and score >= TARGET),
        "error": err,
        "score_version": data.get("score_version"),
    }
    checks = data.get("checks") or {}
    for cat in CATEGORIES:
        c = checks.get(cat) or {}
        s, m = c.get("score"), c.get("max")
        row[f"{cat}_pct"] = round(100 * s / m) if (not err and s is not None and m) else None
    recs = data.get("recommendations") or []
    row["recommendations"] = " | ".join(str(r) for r in recs[:8])
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=str(HERE / "sites.txt"))
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--geo-cmd", default=f"uvx --from {PKG} geo",
                    help="command that runs the geo CLI")
    args = ap.parse_args()

    sites = load_sites(Path(args.sites))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    geo_cmd = args.geo_cmd.split()

    rows = []
    for url in sites:
        print(f"Auditing {url} ...", flush=True)
        row = to_row(url, run_at, audit(url, geo_cmd, args.timeout))
        print(f"  -> {row['score'] if row['error'] is None else 'ERROR: ' + row['error']}", flush=True)
        rows.append(row)

    (out / "geo-latest.json").write_text(json.dumps(rows, indent=2))
    hist_path = out / "geo-history.json"
    history = json.loads(hist_path.read_text()) if hist_path.exists() else []
    history = [r for r in history if r.get("run_at") is not None] + rows
    hist_path.write_text(json.dumps(history, indent=2))

    below = [r["site"] for r in rows if not r["meets_target"]]
    print(f"\n{len(rows) - len(below)}/{len(rows)} sites at or above {TARGET}.")
    if below:
        print("Below target or failed: " + ", ".join(below))
    return 0


if __name__ == "__main__":
    sys.exit(main())

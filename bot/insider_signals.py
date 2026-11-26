"""Copy-trading source: insider open-market purchases (SEC Form 4). Data only, no orders.

Company officers and directors must report their own trades within two
business days. Open-market PURCHASES (transaction code P) are the classic
"insiders put their own money in" signal. Two sources, same event format:

  * history: SEC "Insider Transactions Data Sets" (quarterly TSV zips) for
    backtests, downloaded once into %LOCALAPPDATA%\\horsesdontstop\\insider-data;
  * live: the EDGAR daily index, crawling each new Form 4 filing.

Events: {"filed": date, "symbol", "owner", "title", "value", "accession"}.
Only officers/directors (not 10%-holder funds), price >= $2, value >= $10k
per filing. Downloads are parsed as text; nothing in them is executed.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

RUNTIME = Path(os.environ.get("LOCALAPPDATA", ".")) / "horsesdontstop"
DATA = RUNTIME / "insider-data"
EVENTS = RUNTIME / "insider-events.csv"
SIGNALS = RUNTIME / "insider-signals.json"
UA = "HorsesDontStop research bot (paper trading)"
LISTING = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
MIN_PRICE, MIN_VALUE = 2.0, 10_000.0
FIELDS = ("filed", "symbol", "owner", "title", "value", "accession", "source")


def _get(url: str, retries: int = 3) -> bytes:
    import urllib.request
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))
    return b""


def _is_insider(relationship: str) -> bool:
    rel = (relationship or "").lower()
    return "director" in rel or "officer" in rel


# ------------------------------------------------------------------ history (bulk)

def dataset_urls() -> dict[str, str]:
    html = _get(LISTING).decode("utf-8", "replace")
    out = {}
    for href in re.findall(r'href="([^"]*?(\d{4}q\d)_form345\.zip)"', html):
        out[href[1]] = "https://www.sec.gov" + href[0] if href[0].startswith("/") else href[0]
    return out


def download_history(since: str = "2021q1", folder: Path = DATA) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for q, url in sorted(dataset_urls().items()):
        if q < since:
            continue
        path = folder / f"{q}_form345.zip"
        if not path.exists():
            data = _get(url)
            if not data.startswith(b"PK"):
                raise ValueError(f"{q}: not a zip")
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            time.sleep(0.5)
        paths.append(path)
    return paths


def _tsv(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"),
                                  delimiter="\t", quoting=csv.QUOTE_NONE)


def parse_dataset(path: Path) -> list[dict]:
    z = zipfile.ZipFile(path)
    subs = {}
    for r in _tsv(z, "SUBMISSION.tsv"):
        if r.get("DOCUMENT_TYPE") == "4" and r.get("ISSUERTRADINGSYMBOL"):
            try:
                filed = datetime.strptime(r["FILING_DATE"], "%d-%b-%Y").date()
            except ValueError:
                continue
            subs[r["ACCESSION_NUMBER"]] = (filed, r["ISSUERTRADINGSYMBOL"].strip().upper())
    owners = {}
    for r in _tsv(z, "REPORTINGOWNER.tsv"):
        acc = r["ACCESSION_NUMBER"]
        if acc in subs and _is_insider(r.get("RPTOWNER_RELATIONSHIP", "")):
            owners.setdefault(acc, (r.get("RPTOWNERNAME", "").strip(),
                                    (r.get("RPTOWNER_TITLE") or r.get("RPTOWNER_RELATIONSHIP") or "").strip()))
    value = {}
    for r in _tsv(z, "NONDERIV_TRANS.tsv"):
        acc = r["ACCESSION_NUMBER"]
        if acc not in owners or r.get("TRANS_CODE") != "P" or r.get("TRANS_ACQUIRED_DISP_CD") != "A":
            continue
        try:
            shares, price = float(r["TRANS_SHARES"]), float(r["TRANS_PRICEPERSHARE"])
        except (TypeError, ValueError):
            continue
        if price >= MIN_PRICE and shares > 0:
            value[acc] = value.get(acc, 0.0) + shares * price
    out = []
    for acc, v in value.items():
        if v >= MIN_VALUE:
            filed, sym = subs[acc]
            name, title = owners[acc]
            out.append({"filed": filed, "symbol": sym, "owner": name, "title": title,
                        "value": round(v, 2), "accession": acc, "source": path.stem})
    return out


# ------------------------------------------------------------------ live (daily index)

def _xml_text(node, path):
    el = node.find(path)
    return (el.text or "").strip() if el is not None and el.text else ""


def parse_form4(text: str, accession: str, filed: date) -> list[dict]:
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text, re.S)
    if not m:
        return []
    root = ET.fromstring(m.group(0))
    sym = _xml_text(root, "issuer/issuerTradingSymbol").upper()
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    flags = {k: _xml_text(rel, k).lower() in ("1", "true") for k in ("isDirector", "isOfficer")} if rel is not None else {}
    if not sym or not any(flags.values()):
        return []
    owner = _xml_text(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    title = _xml_text(rel, "officerTitle") or ("Director" if flags.get("isDirector") else "Officer")
    total = 0.0
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if (_xml_text(t, "transactionCoding/transactionCode") != "P"
                or _xml_text(t, "transactionAmounts/transactionAcquiredDisposedCode/value") != "A"):
            continue
        try:
            shares = float(_xml_text(t, "transactionAmounts/transactionShares/value"))
            price = float(_xml_text(t, "transactionAmounts/transactionPricePerShare/value"))
        except ValueError:
            continue
        if price >= MIN_PRICE and shares > 0:
            total += shares * price
    if total < MIN_VALUE:
        return []
    return [{"filed": filed, "symbol": sym, "owner": owner, "title": title,
             "value": round(total, 2), "accession": accession, "source": "edgar-daily"}]


def crawl_day(day: date, pause: float = 0.12) -> list[dict]:
    q = (day.month - 1) // 3 + 1
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx"
    try:
        index = _get(url).decode("latin-1")
    except Exception:
        return []                                  # no index: holiday/weekend or not yet posted
    files = []
    for line in index.splitlines():
        if line[:12].strip() == "4":
            m = re.search(r"(edgar/data/\S+\.txt)", line)
            if m:
                files.append(m.group(1))
    out, seen = [], set()
    for f in dict.fromkeys(files):
        acc = f.rsplit("/", 1)[-1][:-4]
        if acc in seen:
            continue
        seen.add(acc)
        try:
            out.extend(parse_form4(_get(f"https://www.sec.gov/Archives/{f}").decode("utf-8", "replace"),
                                   acc, day))
        except Exception:
            pass
        time.sleep(pause)
    return out


# ------------------------------------------------------------------ storage

def load_events(path: Path = EVENTS) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{**r, "filed": date.fromisoformat(r["filed"]), "value": float(r["value"])}
                for r in csv.DictReader(f)]


def save_events(events, path: Path = EVENTS):
    seen, rows = set(), []
    for e in sorted(events, key=lambda e: (e["filed"], e["symbol"], e["accession"])):
        k = (e["accession"], e["owner"])
        if k not in seen:
            seen.add(k)
            rows.append(e)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for e in rows:
            w.writerow({**e, "filed": e["filed"].isoformat()})
    os.replace(tmp, path)
    return rows


def by_symbol(events) -> dict[str, list[dict]]:
    out = {}
    for e in events:
        out.setdefault(e["symbol"], []).append(e)
    return out


def current_signals(events, sessions: list[date], *, valid_sessions=3, min_value=50_000.0) -> dict:
    """{symbol: score} for purchases filed in the last `valid_sessions` sessions."""
    from math import log10
    if not sessions:
        return {}
    first = sessions[-valid_sessions] if len(sessions) >= valid_sessions else sessions[0]
    recent = [e for e in events if e["filed"] >= first]
    out = {}
    for sym, evs in by_symbol(recent).items():
        value = sum(e["value"] for e in evs)
        if value >= min_value:
            out[sym] = round(log10(value) + 0.5 * (len({e["owner"] for e in evs}) - 1), 4)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backfill", action="store_true", help="download + parse quarterly history")
    ap.add_argument("--daily", type=int, default=0, help="crawl the last N calendar days")
    a = ap.parse_args(argv)
    events = load_events()
    if a.backfill:
        for path in download_history():
            events.extend(parse_dataset(path))
            print(f"{path.name}: total events {len(events)}")
    if a.daily:
        done_file = RUNTIME / "insider-crawled-days.json"
        done = set(json.loads(done_file.read_text())) if done_file.exists() else set()
        today = date.today()
        for k in range(a.daily, 0, -1):
            day = today - timedelta(days=k)
            if day.weekday() >= 5 or day.isoformat() in done:
                continue
            new = crawl_day(day)
            events.extend(new)
            done.add(day.isoformat())
            print(f"{day}: {len(new)} insider purchases")
        done_file.write_text(json.dumps(sorted(done)[-60:]))
    events = save_events(events)
    from .daily_market_recorder import default_folder, read_history
    sessions = [b.day for b in read_history(default_folder(), ["SPY"])["SPY"]]
    sig = current_signals(events, sessions)
    SIGNALS.write_text(json.dumps({"generated": datetime.now().isoformat(), "through": str(sessions[-1]) if sessions else None,
                                   "signals": sig}, indent=1), encoding="utf-8")
    print(f"INSIDER: {len(events)} purchase events stored; {len(sig)} live signals")
    return 0


if __name__ == "__main__":
    sys.exit(main())

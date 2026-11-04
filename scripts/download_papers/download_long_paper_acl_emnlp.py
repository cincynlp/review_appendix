import csv
import hashlib
import json
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import fitz
import requests
from bs4 import BeautifulSoup

TARGET = 500
MIN_PAGES = 25
SEED = 42

OUTPUT = Path("acl_2026_500")
PDF_DIR = OUTPUT / "pdfs"
MANIFEST = OUTPUT / "manifest_full.json"
VOLUMES = {"2026.acl-long": "main"}

# OUTPUT = Path("emnlp_2026_500")
# PDF_DIR = OUTPUT / "pdfs"
# MANIFEST = OUTPUT / "manifest_full.json"
# VOLUMES = {"2026.emnlp-main": "main"}

PAUSE_EVERY = 100  # All downloaded PDFs count, including those below 30 pages.
PAUSE_SECONDS = 3600
REQUEST_DELAY = 3


def wait_until(deadline):
    while time.time() < deadline:
        remaining = max(0, int(deadline - time.time()))
        print(f"\rWaiting {remaining // 60}m {remaining % 60}s...", end="", flush=True)
        time.sleep(min(30, max(0.1, deadline - time.time())))
    print()


def fetch(session, url):
    for attempt in range(6):
        time.sleep(REQUEST_DELAY)
        try:
            response = session.get(url, timeout=(30, 180))
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 5:
                raise
            wait_until(time.time() + 10 * 2**attempt)
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt < 5:
            seconds = 3600 if response.status_code == 429 else 10 * 2**attempt
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    seconds = float(retry_after)
                except ValueError:
                    try:
                        reset = parsedate_to_datetime(retry_after)
                        if reset.tzinfo is None:
                            reset = reset.replace(tzinfo=timezone.utc)
                        seconds = (reset - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        pass
            print(f"\nHTTP {response.status_code}; waiting before retry.")
            response.close()
            wait_until(time.time() + max(1, seconds) + 5)
            continue
        response.raise_for_status()
        return response.content
    raise RuntimeError(f"Could not retrieve {url}")


def save(state):
    temporary = MANIFEST.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(MANIFEST)
    with (OUTPUT / "report.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "track", "title", "pages", "status", "error"],
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(p for p in state["papers"] if p.get("status") or p.get("error"))


def discover(session):
    papers = {}
    for volume, track in VOLUMES.items():
        url = f"https://aclanthology.org/volumes/{volume}/"
        cache = OUTPUT / f"{volume}.html"
        if not cache.exists():
            cache.write_bytes(fetch(session, url))
        soup = BeautifulSoup(cache.read_bytes(), "html.parser")
        pattern = re.compile(r"/" + re.escape(volume) + r"\.([1-9]\d*)/?")
        found = set()
        for anchor in soup.find_all("a", href=True):
            parsed = urlparse(urljoin(url, anchor["href"]))
            match = pattern.fullmatch(parsed.path)
            if parsed.hostname != "aclanthology.org" or not match:
                continue
            title = anchor.get_text(" ", strip=True)
            if not title or title.lower() in {"pdf", "bib", "cite", "abstract", "anthology"}:
                continue
            identifier = f"{volume}.{match.group(1)}"
            found.add(identifier)
            if identifier not in papers or len(title) > len(papers[identifier]["title"]):
                papers[identifier] = {
                    "id": identifier, "track": track, "title": title,
                    "pdf_url": f"https://aclanthology.org/{identifier}.pdf",
                }
        if not found:
            raise RuntimeError(f"No paper records found at {url}; check the page before continuing.")
        print(f"Found {len(found)} {track} papers.")
    ordered = sorted(papers.values(), key=lambda p: p["id"])
    random.Random(SEED).shuffle(ordered)
    return ordered


def main():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "ACLResearchDownloader/1.0"
    if MANIFEST.exists():
        state = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if state["seed"] != SEED or state["min_pages"] != MIN_PAGES:
            raise RuntimeError("Use a new OUTPUT folder when changing SEED or MIN_PAGES.")
        print("Resuming the saved sample order.")
    else:
        state = {"seed": SEED, "min_pages": MIN_PAGES, "papers": discover(session),
                 "download_count": 0, "next_allowed_at": 0}
        save(state)

    for paper in state["papers"]:
        if paper.get("status") == "saved":
            path = PDF_DIR / f"{paper['id']}.pdf"
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != paper.get("sha256"):
                paper.pop("status", None)
    kept = sum(p.get("status") == "saved" for p in state["papers"])
    if kept > TARGET:
        raise RuntimeError("Existing sample exceeds TARGET; use a new OUTPUT folder.")
    save(state)

    for index, paper in enumerate(state["papers"], 1):
        if kept >= TARGET:
            break
        if paper.get("status") in {"saved", "below_20_pages"}:
            continue
        try:
            if state["next_allowed_at"] > time.time():
                wait_until(state["next_allowed_at"])
            pdf = fetch(session, paper["pdf_url"])
            state["download_count"] += 1
            if state["download_count"] % PAUSE_EVERY == 0:
                state["next_allowed_at"] = time.time() + PAUSE_SECONDS
            save(state)
            if not pdf.startswith(b"%PDF"):
                raise ValueError("Downloaded response is not a PDF.")
            with fitz.open(stream=pdf, filetype="pdf") as document:
                paper["pages"] = len(document)
            if paper["pages"] >= MIN_PAGES:
                destination = PDF_DIR / f"{paper['id']}.pdf"
                temporary = destination.with_suffix(".part")
                temporary.write_bytes(pdf)
                temporary.replace(destination)
                paper["sha256"] = hashlib.sha256(pdf).hexdigest()
                paper["status"] = "saved"
                kept += 1
            else:
                paper["status"] = "below_20_pages"
            paper.pop("error", None)
            save(state)
            print(f"[{index}/{len(state['papers'])}] {paper['id']}: {paper['pages']} pages | "
                  f"{paper['status']} | saved {kept}/{TARGET}", flush=True)
        except Exception as exc:
            paper["error"] = str(exc)
            paper.pop("status", None)
            save(state)
            print("Stopped. Rerun the script to resume at this paper.")
            raise

    print(f"\nSaved {kept}/{TARGET} PDFs in {PDF_DIR.resolve()}")
    if kept < TARGET:
        print("The combined pool was exhausted before reaching the target.")


if __name__ == "__main__":
    main()

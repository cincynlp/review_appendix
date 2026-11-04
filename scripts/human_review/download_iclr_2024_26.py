import json
import random
import time
import getpass
from pathlib import Path

import openreview
from tqdm import tqdm

# ==========================
# CONFIGURATION
# ==========================

YEARS = [2024, 2025, 2026]

PAPERS_PER_YEAR = 1500
SEED = 42

# Same output directory as your previous script
OUTPUT = Path("iclr_2021_2026_papers")
PDF_DIR = OUTPUT / "pdfs"

OUTPUT.mkdir(parents=True, exist_ok=True)
PDF_DIR.mkdir(parents=True, exist_ok=True)

JSONL_FILE = OUTPUT / "iclr_2016_2026_papers_with_reviews.jsonl"

# ==========================
# CONNECT TO OPENREVIEW API V2
# ==========================

client = openreview.api.OpenReviewClient(
    baseurl="https://api2.openreview.net",
    username=input("OpenReview email: ").strip(),
    password=getpass.getpass("OpenReview password: "),
)

# ==========================
# HELPER FUNCTIONS
# ==========================

def get_value(content, key, default=""):
    """Extract values from OpenReview API V2 content."""
    item = content.get(key, default)

    if isinstance(item, dict):
        return item.get("value", default)

    return item


def get_submissions(year):

    venue_id = f"ICLR.cc/{year}/Conference"

    venue = client.get_group(venue_id)

    submission_name = venue.content["submission_name"]["value"]

    invitation = f"{venue_id}/-/{submission_name}"

    print(f"\nICLR {year}")
    print(f"Submission invitation: {invitation}")

    submissions = client.get_all_notes(
        invitation=invitation
    )

    # Keep publicly accessible submissions with PDFs
    eligible = [
        note for note in submissions
        if "everyone" in (note.readers or [])
        and get_value(note.content, "pdf")
    ]

    print(f"Total submissions: {len(submissions)}")
    print(f"Eligible papers: {len(eligible)}")

    return eligible


def get_reviews(paper_id):

    notes = client.get_all_notes(
        forum=paper_id
    )

    reviews = []

    for note in notes:

        invitations = getattr(note, "invitations", []) or []

        is_official_review = any(
            inv.endswith("/Official_Review")
            for inv in invitations
        )

        if not is_official_review:
            continue

        # Preserve all original review fields
        content = {
            key: get_value(note.content, key)
            for key in note.content
        }

        reviews.append({
            "review_id": note.id,
            "reviewer": note.signatures,
            "content": content,
            "timestamp": note.tcdate,
        })

    return reviews


def load_existing_records():

    records = {}

    if not JSONL_FILE.exists():
        return records

    with open(JSONL_FILE, "r", encoding="utf-8") as f:

        for line in f:
            if not line.strip():
                continue

            record = json.loads(line)

            key = (
                record["year"],
                record["paper_id"]
            )

            records[key] = record

    return records


def save_record(record):

    with open(JSONL_FILE, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(record, ensure_ascii=False) + "\n"
        )


# ==========================
# LOAD PREVIOUS DATA
# ==========================

existing_records = load_existing_records()

print(f"\nExisting records: {len(existing_records)}")

# ==========================
# PROCESS NEW YEARS
# ==========================

for year in YEARS:

    submissions = get_submissions(year)

    # Deterministic order before sampling
    submissions = sorted(
        submissions,
        key=lambda note: note.id
    )

    rng = random.Random(SEED + year)

    selected = rng.sample(
        submissions,
        min(PAPERS_PER_YEAR, len(submissions))
    )

    print(f"Selected {len(selected)} papers from ICLR {year}")

    for paper in tqdm(selected, desc=f"Downloading ICLR {year}"):

        paper_id = paper.id

        key = (year, paper_id)

        pdf_path = PDF_DIR / f"{year}_{paper_id}.pdf"

        # Skip already completed records
        if (
            key in existing_records
            and "error" not in existing_records[key]
            and pdf_path.exists()
            and pdf_path.stat().st_size > 0
        ):
            continue

        record = {
            "year": year,
            "paper_id": paper_id,
            "title": get_value(paper.content, "title"),
            "abstract": get_value(paper.content, "abstract"),
            "pdf_url": f"https://openreview.net/pdf?id={paper_id}",
            "forum_url": f"https://openreview.net/forum?id={paper_id}",
            "pdf_file": str(pdf_path),
            "reviews": [],
        }

        try:

            # ==========================
            # DOWNLOAD PDF
            # ==========================

            if not pdf_path.exists():

                pdf = client.get_attachment(
                    id=paper_id,
                    field_name="pdf"
                )

                if not pdf.startswith(b"%PDF"):
                    raise ValueError("Invalid PDF content")

                pdf_path.write_bytes(pdf)

            # ==========================
            # RETRIEVE REVIEWS
            # ==========================

            record["reviews"] = get_reviews(paper_id)

            # ==========================
            # SAVE
            # ==========================

            save_record(record)

            existing_records[key] = record

        except Exception as e:

            print(f"Failed: {paper_id}: {e}")

            record["error"] = str(e)

            # Failed records are not appended;
            # they will be retried on the next run.

        time.sleep(0.2)

# ==========================
# FINAL SUMMARY
# ==========================

print("\n=== COMPLETE ===")

print(f"Total papers: {len(existing_records)}")

print(
    "Total reviews:",
    sum(
        len(p["reviews"])
        for p in existing_records.values()
    )
)

for year in range(2021, 2027):

    papers = [
        p for p in existing_records.values()
        if p["year"] == year
    ]

    reviews = sum(
        len(p["reviews"])
        for p in papers
    )

    print(
        f"ICLR {year}: "
        f"{len(papers)} papers, "
        f"{reviews} reviews"
    )
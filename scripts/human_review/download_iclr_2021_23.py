import json
import random
import time
from pathlib import Path

import openreview
from tqdm import tqdm

# ==========================
# CONFIGURATION
# ==========================

YEARS = [2021, 2022, 2023]

PAPERS_PER_YEAR = 2000
SEED = 42

OUTPUT = Path("iclr_2021_2026_papers")
PDF_DIR = OUTPUT / "pdfs"

OUTPUT.mkdir(parents=True, exist_ok=True)
PDF_DIR.mkdir(parents=True, exist_ok=True)

import openreview
import getpass

client = openreview.Client(
    baseurl="https://api.openreview.net",
    username=input("OpenReview email: ").strip(),
    password=getpass.getpass("OpenReview password: "),
)

def get_submissions(year):

    venue = f"ICLR.cc/{year}/Conference"

    # Retrieve blind submissions.
    submissions = client.get_all_notes(
        invitation=f"{venue}/-/Blind_Submission"
    )

    print(f"ICLR {year}: {len(submissions)} submissions")

    return submissions


def get_reviews(paper_id):

    # Get all public notes in the paper's discussion forum.
    notes = client.get_all_notes(
        forum=paper_id
    )

    reviews = []

    for note in notes:

        if any(
            inv.endswith("/Official_Review")
            for inv in note.invitation.split()
        ):
            reviews.append({
                "review_id": note.id,
                "reviewer": note.signatures,
                "content": note.content,
                "timestamp": note.tcdate,
            })

    return reviews


all_records = []

for year in YEARS:

    submissions = get_submissions(year)

    rng = random.Random(SEED + year)

    selected = rng.sample(
        submissions,
        min(PAPERS_PER_YEAR, len(submissions))
    )

    for paper in tqdm(selected, desc=f"ICLR {year}"):

        paper_id = paper.id

        pdf_path = PDF_DIR / f"{year}_{paper_id}.pdf"

        record = {
            "year": year,
            "paper_id": paper_id,
            "title": paper.content.get("title", ""),
            "abstract": paper.content.get("abstract", ""),
            "pdf_url": f"https://openreview.net/pdf?id={paper_id}",
            "forum_url": f"https://openreview.net/forum?id={paper_id}",
            "pdf_file": str(pdf_path),
            "reviews": [],
        }

        try:
            # Download paper.
            if not pdf_path.exists():

                pdf = client.get_attachment(
                    id=paper_id,
                    field_name="pdf"
                )

                pdf_path.write_bytes(pdf)

            # Retrieve official reviews.
            record["reviews"] = get_reviews(paper_id)

        except Exception as e:
            print(f"Failed: {paper_id}: {e}")
            record["error"] = str(e)

        all_records.append(record)

        # Save after each paper for resumability.
        with open(
            OUTPUT / "iclr_2016_2026_papers_with_reviews.jsonl",
            "w",
            encoding="utf-8"
        ) as f:

            for item in all_records:
                f.write(
                    json.dumps(item, ensure_ascii=False) + "\n"
                )

        time.sleep(0.2)

print("\n=== COMPLETE ===")

print(f"Total papers: {len(all_records)}")

print(
    "Total reviews:",
    sum(len(p["reviews"]) for p in all_records)
)
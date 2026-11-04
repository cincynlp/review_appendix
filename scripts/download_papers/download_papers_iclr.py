import hashlib
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import openreview

VENUE = "ICLR.cc/2027/Conference"
N = 1000
SEED = 42
OUTPUT = Path("iclr_2027_sample")

import getpass
import openreview

client = openreview.api.OpenReviewClient(
    baseurl="https://api2.openreview.net",
    username=input("OpenReview email: ").strip(),
    password=getpass.getpass("OpenReview password: "),
)

# Discover the venue's submission invitation.
venue = client.get_group(VENUE)
submission_name = venue.content["submission_name"]["value"]

submissions = client.get_all_notes(
    invitation=f"{VENUE}/-/{submission_name}"
)

# Sampling population: publicly readable submissions with a PDF.
eligible = sorted(
    [
        note for note in submissions
        if "everyone" in (note.readers or [])
        and note.content.get("pdf", {}).get("value")
    ],
    key=lambda note: note.id,
)

print(f"Found {len(eligible)} public submissions with PDFs.")

if len(eligible) < N:
    raise RuntimeError(
        f"Need {N} papers, but only {len(eligible)} are accessible. "
        "Submissions may not yet be public."
    )

selected = random.Random(SEED).sample(eligible, N)

# Prevent accidentally overwriting an existing experimental sample.
OUTPUT.mkdir(parents=True, exist_ok=False)
pdf_dir = OUTPUT / "pdfs"
pdf_dir.mkdir()

manifest = {
    "venue": VENUE,
    "seed": SEED,
    "sampled_at_utc": datetime.now(timezone.utc).isoformat(),
    "eligible_count": len(eligible),
    "eligible_ids": [note.id for note in eligible],
    "papers": [
        {
            "id": note.id,
            "number": note.number,
            "title": note.content.get("title", {}).get("value", ""),
            "abstract": note.content.get("abstract", {}).get("value", ""),
            "status": note.content.get("venueid", {}).get("value", ""),
            "pdf_reference": note.content["pdf"]["value"],
            "forum_url": f"https://openreview.net/forum?id={note.id}",
        }
        for note in selected
    ],
}

manifest_path = OUTPUT / "manifest.json"


def save_manifest():
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


save_manifest()

for index, paper in enumerate(manifest["papers"], start=1):
    try:
        pdf = client.get_attachment(id=paper["id"], field_name="pdf")

        if not pdf.startswith(b"%PDF"):
            raise ValueError("Downloaded content is not a PDF.")

        filename = f"{paper['id']}.pdf"
        (pdf_dir / filename).write_bytes(pdf)

        paper["local_file"] = f"pdfs/{filename}"
        paper["sha256"] = hashlib.sha256(pdf).hexdigest()
        paper["downloaded_at_utc"] = datetime.now(timezone.utc).isoformat()

        print(f"[{index}/{N}] Downloaded: {paper['title']}")

    except Exception as exc:
        paper["download_error"] = str(exc)
        print(f"[{index}/{N}] FAILED: {paper['id']} — {exc}")

    save_manifest()
    time.sleep(30)  # Space out requests.

successful = sum("local_file" in p for p in manifest["papers"])
print(f"\nDownloaded {successful}/{N} PDFs to {OUTPUT.resolve()}")
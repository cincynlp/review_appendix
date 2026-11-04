import json
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from collections import defaultdict

from tqdm import tqdm

# ==========================
# CONFIGURATION
# ==========================

YEARS = [2021, 2022, 2023, 2024, 2025, 2026]

PAPERS_PER_YEAR = 200
SEED = 42

BASE_DIR = Path("iclr_2021_2026")

PDF_DIR = BASE_DIR / "pdf_app"
REVIEW_FILE = BASE_DIR / "papers_with_reviews.jsonl"

MODEL = "gpt-6.1-sol"
REASONING_EFFORT = "low"   # set to None to use Codex's default

# Separate output file so results don't mix with the gpt-5-mini run
OUTPUT_FILE = BASE_DIR / "human_review_citation_iclr_gptsol.jsonl"

# Same sample file as the API script -> same papers and same reviews
SAMPLE_FILE = BASE_DIR / "iclr_100_papers_1_review_per_year_new.json"

USAGE_LIMIT_WAIT_MINUTES = 15

# ==========================
# PROMPT
# ==========================

PROMPT = """You are given a research paper and an original human-written peer review of that paper.

Your task is to identify the section of the paper that each weakness in the review is directed at.

Definitions:
- Main body: everything before the References section.
- Appendix: everything after the References section.
- An item is directed at a section if that section contains the content the item praises or criticizes (a claim, method, result, table, figure, proof, or experimental detail).

Instructions:
1. Use the reviewer's own enumeration. Each bullet or numbered point in the
   Weaknesses section is ONE weakness; do not split it into several items.
3. Number weaknesses as W1, W2, W3, etc.
4. For each item, give the ONE section it is primarily directed at.
5. Give TWO sections only if the item explicitly relies on content from BOTH the main body AND the appendix
(e.g., "Table 1 reports X, but Appendix E shows Y").
Never give more than two sections.
6. Use the paper's own top-level section numbers and titles:
   - Main body: "Section <number> - <title>"
   - Appendix: "Appendix <letter> - <title>"
   Use top-level sections only (Section 3, not Section 3.2).
7. Do not invent sections.
8. If no relevant section can be identified confidently, write "not found".
9. Do not rewrite, modify, or reassess the original review.
10. Do not make assumptions. Base every answer only on what the review explicitly says.
11. Assign a weakness to the Appendix ONLY if it is very obvious that it is directed at appendix content
    (e.g., the reviewer explicitly mentions the appendix, or the criticized table, figure, proof, or experiment
    exists only in the appendix). Generic comments must NOT be assigned to the Appendix.
12. Consider only the weaknesses expressed by the reviewer.
    Do not treat neutral statements, summaries, ratings, or QUESTION TO THE AUTHORS as weaknesses.


Return ONLY the following format:

W1 - Section 3 - Methodology
W2 - Section 6 - Analysis; Appendix E - Ablations
W3 - Appendix B - Proofs
W4 - not found

ORIGINAL HUMAN REVIEW:
"""


# ==========================
# HELPER: EXTRACT REVIEW TEXT
# ==========================

def get_value(value):
    """
    Handles both OpenReview API V1 and V2 content.
    """
    if isinstance(value, dict) and "value" in value:
        return value["value"]

    return value


def extract_review_text(review):
    """
    Convert a human review's content into readable text.

    Works with both older ICLR reviews and newer
    structured reviews.
    """

    content = review.get("content", {})

    if isinstance(content, str):
        return content

    if not isinstance(content, dict):
        return str(content)

    # Exclude metadata fields that are not review arguments
    exclude = {
        "rating",
        "confidence",
        "soundness",
        "presentation",
        "contribution",
        "recommendation",
        "correctness",
        "technical_novelty_and_significance",
        "empirical_novelty_and_significance",
        "code_of_ethics",
        "ethics_review",
        "reviewer_expertise",
        "reviewer_confidence",
    }

    parts = []

    for key, value in content.items():

        if key.lower() in exclude:
            continue

        value = get_value(value)

        if value is None:
            continue

        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)

        value = str(value).strip()

        if not value:
            continue

        heading = key.replace("_", " ").title()

        parts.append(f"{heading}:\n{value}")

    return "\n\n".join(parts)


# ==========================
# HELPER: RUN CODEX
# ==========================

USAGE_LIMIT_PHRASES = (
    "usage limit",
    "usage_limit",
    "rate limit",
    "rate_limit",
    "too many requests",
)


def run_codex(pdf_path, review_text):
    """
    Copy the PDF into a temp folder, run `codex exec` there in a
    read-only sandbox, and return the final message text.
    Waits and retries when a usage/rate limit is hit.
    """

    with tempfile.TemporaryDirectory(prefix="paper_citations_") as folder:

        shutil.copy2(pdf_path, Path(folder) / pdf_path.name)
        citation_path = Path(folder) / "citations.txt"

        prompt = (
            f"The paper is the PDF file `{pdf_path.name}` "
            f"in the current directory.\n\n"
            + PROMPT
            + review_text
        )

        command = [
            "codex", "exec",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--model", MODEL,
        ]

        if REASONING_EFFORT:
            command += ["-c", f'model_reasoning_effort="{REASONING_EFFORT}"']

        command += ["--output-last-message", str(citation_path), "-"]

        while True:

            # Remove any partial output from a failed attempt
            if citation_path.exists():
                citation_path.unlink()

            result = subprocess.run(
                command,
                input=prompt,
                cwd=folder,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            if result.returncode == 0:
                break

            error_text = (result.stdout + "\n" + result.stderr).lower()

            if any(p in error_text for p in USAGE_LIMIT_PHRASES):
                tqdm.write(
                    f"Usage limit reached for {pdf_path.name}. "
                    f"Waiting {USAGE_LIMIT_WAIT_MINUTES} minutes before retrying..."
                )
                time.sleep(USAGE_LIMIT_WAIT_MINUTES * 60)
                continue

            raise RuntimeError(
                f"Codex exited with code {result.returncode}. "
                f"{error_text[-2000:]}"
            )

        if not citation_path.exists():
            raise RuntimeError("Codex did not write an output file.")

        citations = citation_path.read_text(encoding="utf-8")

    if not citations.strip():
        raise RuntimeError("Codex returned empty citations.")

    return citations


# ==========================
# LOAD PAPER + REVIEW DATA
# ==========================

papers_by_year = defaultdict(dict)

with REVIEW_FILE.open("r", encoding="utf-8") as f:

    for line in f:

        if not line.strip():
            continue

        record = json.loads(line)

        year = int(record["year"])
        paper_id = record["paper_id"]

        if year not in YEARS:
            continue

        pdf_path = PDF_DIR / f"{year}_{paper_id}.pdf"

        if not pdf_path.exists():
            continue

        if pdf_path.stat().st_size == 0:
            continue

        reviews = record.get("reviews", [])

        if not reviews:
            continue

        # Keep only reviews with usable text
        valid_reviews = [
            review for review in reviews
            if extract_review_text(review).strip()
        ]

        if not valid_reviews:
            continue

        record["reviews"] = valid_reviews

        papers_by_year[year][paper_id] = record


# ==========================
# SAMPLE 100 PAPERS PER YEAR, 1 REVIEW PER PAPER
# ==========================

if SAMPLE_FILE.exists():

    print("Loading previously saved sample.")

    with SAMPLE_FILE.open("r", encoding="utf-8") as f:
        sample = json.load(f)

else:

    sample = {}

    for year in YEARS:

        available_ids = sorted(papers_by_year[year])

        # Take up to PAPERS_PER_YEAR; if fewer are available, take all of them
        n = min(PAPERS_PER_YEAR, len(available_ids))

        if n < PAPERS_PER_YEAR:
            print(
                f"ICLR {year}: only {len(available_ids)} eligible papers, "
                f"taking all of them."
            )

        rng = random.Random(SEED + year)

        sampled_ids = rng.sample(available_ids, n)

        year_sample = {}

        for paper_id in sampled_ids:

            reviews = sorted(
                papers_by_year[year][paper_id]["reviews"],
                key=lambda r: str(r.get("review_id", ""))
            )

            chosen = rng.choice(reviews)

            year_sample[paper_id] = chosen.get("review_id")

        sample[str(year)] = year_sample

    with SAMPLE_FILE.open("w", encoding="utf-8") as f:
        json.dump(sample, f, indent=2)


# ==========================
# BUILD TASK LIST (PAPER, REVIEW)
# ==========================

tasks = []

for year in YEARS:

    year_sample = sample.get(str(year), {})

    for paper_id, review_id in year_sample.items():

        record = papers_by_year[year].get(paper_id)

        if record is None:
            print(f"Warning: Missing sampled paper {year}_{paper_id}")
            continue

        review = next(
            (r for r in record["reviews"] if r.get("review_id") == review_id),
            None
        )

        if review is None:
            print(
                f"Warning: Missing sampled review {review_id} "
                f"for {year}_{paper_id}"
            )
            continue

        tasks.append((record, review))

print("\n=== SELECTED PAPERS (1 review each) ===")

for year in YEARS:

    count = sum(int(p["year"]) == year for p, _ in tasks)

    if count == 0:
        print(f"ICLR {year}: skipped")
    else:
        print(f"ICLR {year}: {count} papers")

print(f"\nTotal review tasks: {len(tasks)}")


# ==========================
# LOAD ALREADY PROCESSED
# ==========================

processed = set()

if OUTPUT_FILE.exists():

    with OUTPUT_FILE.open("r", encoding="utf-8") as f:

        for line in f:

            if not line.strip():
                continue

            record = json.loads(line)

            if record.get("page_citations", "").strip():
                processed.add((
                    int(record["year"]),
                    record["paper_id"],
                    record["review_id"]
                ))

pending_tasks = [
    (paper, review) for paper, review in tasks
    if (int(paper["year"]), paper["paper_id"], review.get("review_id"))
    not in processed
]

already_done = len(tasks) - len(pending_tasks)

print(f"Already processed (from this sample): {already_done}")
print(f"Remaining: {len(pending_tasks)}")


# ==========================
# PROCESS REVIEWS
# ==========================

completed = 0

with tqdm(
    total=len(tasks),
    initial=already_done,
    desc="Extracting citations",
    unit="review"
) as pbar:

    for paper, review in pending_tasks:

        year = int(paper["year"])
        paper_id = paper["paper_id"]
        review_id = review.get("review_id")

        pdf_path = PDF_DIR / f"{year}_{paper_id}.pdf"

        try:

            citations = run_codex(pdf_path, extract_review_text(review))

            record = {
                "year": year,
                "paper_id": paper_id,
                "review_id": review_id,
                "model": MODEL,
                "page_citations": citations,
            }

            with OUTPUT_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            processed.add((year, paper_id, review_id))
            completed += 1

            tqdm.write(f"Saved: {year}_{paper_id} | Review {review_id}")

        except Exception as error:

            tqdm.write(
                f"Failed: {year}_{paper_id}, "
                f"Review {review_id} — {error}"
            )

        finally:
            pbar.update(1)


# ==========================
# FINAL SUMMARY
# ==========================

print("\n=== COMPLETE ===")

print(f"Selected review tasks: {len(tasks)}")
print(f"Completed this run: {completed}")
print(f"Completed overall (this sample): {already_done + completed}")
print(f"Output: {OUTPUT_FILE}")
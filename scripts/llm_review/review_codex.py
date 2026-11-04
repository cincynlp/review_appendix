import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


PDF_DIR = Path("PATH TO PDFs") # ENTER YOUR PDF DIRECTORY
REVIEW_FILE = Path("reviews_codex_gpt6-astra.jsonl") # SAVE REVIEW PDF
CITATION_FILE = Path("review_citations_codex_gpt6-astra.jsonl") # SAVE WEAKNESS CITATION PDF

MODEL = "gpt-6.1-sol"
# MODEL = "gpt-6-astra"
SAMPLE_PAUSE = 1 * 1
USAGE_PAUSE = 15 * 60

REVIEW_PROMPT = (
    "I am reviewing this paper for ICLR. "
    "Give me an ICLR style review."
)

CITATION_PROMPT = """You are given a research paper and an ICLR-style review that you previously generated for that paper.

Your task is to identify the section of the paper that each weakness in the review is directed at.

Definitions:
- Main body: everything before the References section.
- Appendix: everything after the References section.
- An item is directed at a section if that section contains the content the item praises or criticizes (a claim, method, result, table, figure, proof, or experimental detail).

Instructions:
1. Identify every distinct strength and weakness in the review.
2. Number weaknesses as W1, W2, W3, etc.
3. For each item, give the ONE section it is primarily directed at.
4. Give TWO sections only if the item explicitly relies on content from BOTH the main body AND the appendix 
(e.g., "Table 1 reports X, but Appendix E shows Y"). 
Never give more than two sections.
5. Use the paper's own top-level section numbers and titles:
   - Main body: "Section <number> - <title>"
   - Appendix: "Appendix <letter> - <title>"
   Use top-level sections only (Section 3, not Section 3.2).
6. If an item says something is missing, give the section where that content would belong.
7. Base your answer on where the content actually appears in the paper, 
   not on where the review says it appears.
8. Do not invent sections, and never use the References section.
9. If no relevant section can be identified, write "not found".
10. Do not rewrite, modify, or reassess the original review.

Return ONLY the following format:

W1 - Section 3 - Methodology
W2 - Section 6 - Analysis; Appendix E - Ablations
W3 - Appendix B - Proofs
W4 - not found

Include every weakness from the review.

ORIGINAL REVIEW:
"""


def load_records(path):
    records = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    records[record["paper_id"]] = record
    return records


def save_record(path, record):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_codex(folder, prompt, output_name):
    # Keep response files outside the folder containing the PDF.
    with tempfile.TemporaryDirectory(prefix="codex_output_") as output_folder:
        output_path = Path(output_folder) / output_name

        while True:
            if output_path.exists():
                output_path.unlink()

            result = subprocess.run(
                [
                    "codex", "exec",
                    "--model", MODEL,
                    "-c", 'web_search="disabled"',
                    "--skip-git-repo-check",
                    "--sandbox", "read-only",
                    "--output-last-message", str(output_path),
                    "-",
                ],
                input=prompt,
                cwd=folder,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            if result.stdout:
                print(result.stdout, flush=True)
            if result.stderr:
                print(result.stderr, flush=True)

            if result.returncode == 0:
                output = output_path.read_text(encoding="utf-8")
                if not output.strip():
                    raise RuntimeError("Codex returned an empty response.")
                return output

            error_text = (
                result.stdout + "\n" + result.stderr
            ).lower()

            usage_limit_reached = any(
                phrase in error_text
                for phrase in (
                    "usage limit",
                    "usage_limit",
                    "rate limit",
                    "rate_limit",
                    "too many requests",
                )
            )

            if usage_limit_reached:
                print(
                    "Usage limit reached. Waiting 15 minutes "
                    "before retrying this step...",
                    flush=True,
                )
                time.sleep(USAGE_PAUSE)
                continue

            raise RuntimeError(
                f"Codex exited with code {result.returncode}. "
                f"{error_text[-2000:]}"
            )


# Preserve the original unsorted directory order.
pdf_files = [
    p for p in PDF_DIR.iterdir()
    if p.is_file() and p.suffix.lower() == ".pdf"
][0:100]

if len(pdf_files) != 100:
    raise SystemExit("Expected 100 PDFs at positions 1–100.")

existing_reviews = load_records(REVIEW_FILE)
existing_citations = load_records(CITATION_FILE)

for number, pdf_path in enumerate(pdf_files, start=1):
    paper_id = pdf_path.stem

    review_done = bool(
        existing_reviews.get(paper_id, {})
        .get("generated_review", "").strip()
    )
    citations_done = bool(
        existing_citations.get(paper_id, {})
        .get("page_citations", "").strip()
    )

    if review_done and citations_done:
        print(f"Skipping completed {number}: {paper_id}", flush=True)
        continue

    print(f"Processing {number}: {pdf_path.name}", flush=True)

    try:
        # Both steps receive a folder containing the original raw PDF.
        with tempfile.TemporaryDirectory(prefix="paper_review_") as folder:
            shutil.copy2(pdf_path, Path(folder) / pdf_path.name)

            # STEP 1: Generate or reuse the original review.
            if review_done:
                review = existing_reviews[paper_id]["generated_review"]
                print(f"Reusing saved review: {paper_id}", flush=True)
            else:
                print(f"Generating review: {paper_id}", flush=True)

                review = run_codex(
                    folder,
                    REVIEW_PROMPT,
                    "review.txt",
                )

                review_record = {
                    "paper_id": paper_id,
                    "generated_review": review,
                    "response_id": None,
                    "file_id": None,
                }

                save_record(REVIEW_FILE, review_record)
                existing_reviews[paper_id] = review_record
                print(f"Review saved: {paper_id}", flush=True)

            # STEP 2: Pass the exact saved review back with the PDF.
            if not citations_done:
                print(f"Generating section citations: {paper_id}", flush=True)

                citations = run_codex(
                    folder,
                    CITATION_PROMPT + review,
                    "citations.txt",
                )

                citation_record = {
                    "paper_id": paper_id,
                    "page_citations": citations,
                }

                save_record(CITATION_FILE, citation_record)
                existing_citations[paper_id] = citation_record
                print(f"Section citations saved: {paper_id}", flush=True)

    except Exception as error:
        print(f"Failed: {paper_id} — {error}", flush=True)

    finally:
        print("Pausing 1 second after this paper...", flush=True)
        time.sleep(SAMPLE_PAUSE)
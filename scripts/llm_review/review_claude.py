"""Generate venue-style reviews and per-item section citations with Claude Code (headless mode).

Each paper is reviewed from an empty temporary folder that contains only the anonymized PDF
(named paper.pdf), and the model may only use the Read tool, so no project files or
filenames reach it. Two calls per paper:
  1. review   -> {"paper_id", "generated_review"}
  2. citations -> {"paper_id", "page_citations"}  (section each strength/weakness is directed at)

Requires the `claude` CLI, logged in (ANTHROPIC_API_KEY unset to use your plan).
The run is resumable: papers already present in the output files are skipped.

Examples:
    python review_claude.py --venue ICLR --model claude-opus-5-5   --tag opus55   --pdf-dir PDFS --ids-file ids.txt
    python review_claude.py --venue ACL  --model claude-sonnet-5-5 --tag sonnet55 --pdf-dir PDFS --ids-file ids.txt

Outputs: reviews_<tag>.jsonl and review_citations_<tag>.jsonl (in --out-dir).
"""

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

PAPER_TIMEOUT = 30 * 60   # seconds per call
RETRY_PAUSE = 60 * 60     # wait this long after a usage-limit error, then retry
MAX_TRIES = 3             # attempts per call when the answer is unusable

READ_NOTE = ("The paper is the file paper.pdf in the current directory. Read ALL of its pages with the "
             "Read tool (use the pages parameter, at most 20 pages per call). Use no other tools. ")

CITATION_TEMPLATE = """You are given a research paper and a {venue}-style review that you previously generated for that paper.

Your task is to identify the section of the paper that each strength and weakness in the review is directed at.

Definitions:
- Main body: everything before the References section.
- Appendix: everything after the References section.
- An item is directed at a section if that section contains the content the item praises or criticizes (a claim, method, result, table, figure, proof, or experimental detail).

Instructions:
1. Identify every distinct strength and weakness in the review.
2. Number strengths as S1, S2, S3, etc.
3. Number weaknesses as W1, W2, W3, etc.
4. For each item, give the ONE section it is primarily directed at.
5. Give TWO sections only if the item explicitly relies on content from BOTH the main body AND the appendix
(e.g., "Table 1 reports X, but Appendix E shows Y").
Never give more than two sections.
6. Use the paper's own top-level section numbers and titles:
   - Main body: "Section <number> - <title>"
   - Appendix: "Appendix <letter> - <title>"
   Use top-level sections only (Section 3, not Section 3.2).
7. If an item says something is missing, give the section where that content would belong.
8. Base your answer on where the content actually appears in the paper,
   not on where the review says it appears.
9. Do not invent sections, and never use the References section.
10. If no relevant section can be identified, write "not found".
11. Do not rewrite, modify, or reassess the original review.

Return ONLY the following format:

S1 - Section 3 - Methodology
S2 - Section 5 - Experiments
S3 - Appendix C - Additional Results

W1 - Section 3 - Methodology
W2 - Section 6 - Analysis; Appendix E - Ablations
W3 - Appendix B - Proofs
W4 - not found

Include every strength and weakness from the review.

ORIGINAL REVIEW:
"""


class LimitReached(Exception):
    pass


def review_prompt(venue):
    article = "an" if venue[0].upper() in "AEIOU" else "a"
    return (READ_NOTE + f"I am reviewing this paper for {venue}. Give me {article} {venue} style review. "
            "Return only the review.")


def command(prompt, model):
    return ["claude", "-p", prompt, "--model", model, "--tools", "Read", "--allowedTools", "Read",
            "--no-session-persistence", "--disable-slash-commands", "--setting-sources", "local",
            "--output-format", "stream-json", "--verbose"]


def call_claude(prompt, model, workdir):
    """Run one headless call from workdir and return the final text."""
    proc = subprocess.run(command(prompt, model), cwd=workdir, capture_output=True, text=True,
                          timeout=PAPER_TIMEOUT)
    result = None
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result = event
    if result is None or result.get("is_error"):
        text = (result or {}).get("result", "") + proc.stderr
        if "limit" in text.lower():
            raise LimitReached(text[:300])
        raise RuntimeError(f"claude failed: {text[:300]}")
    return result["result"].strip()


FAILURE_HINTS = ("pdftoppm", "poppler", "wasn't able to read", "was unable to read",
                 "could not read the paper", "couldn't read the paper", "unable to open")


def valid(kind, text):
    """A refusal or a read failure is not a review: reject it so the call is repeated."""
    lowered = text.lower()
    if any(hint in lowered for hint in FAILURE_HINTS):
        return False
    if kind == "review":
        return len(text.split()) >= 600
    return bool(re.search(r"^\W*[SW]1\b", text, re.M))


def checked_call(kind, prompt, model, workdir):
    for attempt in range(1, MAX_TRIES + 1):
        text = call_claude(prompt, model, workdir)
        if valid(kind, text):
            return text
        print(f"  {kind}: unusable answer (attempt {attempt}/{MAX_TRIES}): {text[:120]!r}", flush=True)
    raise RuntimeError(f"{kind}: no usable answer after {MAX_TRIES} tries")


def load_ids(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {r["paper_id"]: r for r in map(json.loads, filter(str.strip, f))}


def append(path, record):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def review_one(paper_id, args, reviews, citations):
    workdir = Path(tempfile.mkdtemp(prefix="claude_review_"))
    try:
        shutil.copy2(args.pdf_dir / f"{paper_id}.pdf", workdir / "paper.pdf")
        if paper_id not in reviews:
            review = checked_call("review", review_prompt(args.venue), args.model, workdir)
            append(args.reviews, {"paper_id": paper_id, "generated_review": review})
            reviews[paper_id] = {"generated_review": review}
        if paper_id not in citations:
            prompt = CITATION_TEMPLATE.format(venue=args.venue) + reviews[paper_id]["generated_review"]
            text = checked_call("citations", prompt, args.model, workdir)
            append(args.citations, {"paper_id": paper_id, "page_citations": text})
            citations[paper_id] = {"page_citations": text}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--venue", required=True, help="venue name used in the prompts, e.g. ICLR, ACL, COLM, EMNLP")
    parser.add_argument("--model", default="claude-opus-5-5", help="Claude model id")
    parser.add_argument("--tag", default="opus55", help="short name used in the output file names")
    parser.add_argument("--pdf-dir", type=Path, required=True, help="folder with <paper_id>.pdf (anonymized)")
    parser.add_argument("--ids-file", type=Path, required=True, help="text file with one paper id per line")
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    parser.add_argument("--limit", type=int, help="only the first N papers of the list")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and command, make no model call")
    args = parser.parse_args()

    args.reviews = args.out_dir / f"reviews_{args.tag}.jsonl"
    args.citations = args.out_dir / f"review_citations_{args.tag}.jsonl"
    args.out_dir.mkdir(parents=True, exist_ok=True)

    ids = [i for i in args.ids_file.read_text().split() if (args.pdf_dir / f"{i}.pdf").exists()][:args.limit]
    reviews, citations = load_ids(args.reviews), load_ids(args.citations)
    todo = [i for i in ids if i not in citations]
    print(f"{len(ids)} papers, {len(todo)} to do, model {args.model}", flush=True)

    if args.dry_run:
        print(" ".join(command("<prompt>", args.model)), "\n(run from an empty temp dir containing paper.pdf)")
        return

    for n, paper_id in enumerate(todo, 1):
        while True:
            try:
                review_one(paper_id, args, reviews, citations)
                break
            except LimitReached as limit:
                print(f"Usage limit reached ({limit}); sleeping {RETRY_PAUSE // 60} min.", flush=True)
                time.sleep(RETRY_PAUSE)
            except Exception as error:
                print(f"{paper_id}: {error}; retrying in 5 min", flush=True)
                time.sleep(300)
        print(f"[{n}/{len(todo)}] {paper_id} done", flush=True)


if __name__ == "__main__":
    main()

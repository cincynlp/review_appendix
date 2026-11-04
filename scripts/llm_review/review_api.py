import json
from pathlib import Path
from tqdm.auto import tqdm
from openai import OpenAI


client = OpenAI(api_key="") # ENTER YOUR API KEY

# MODEL = "gpt-6-astra"
# MODEL = "gpt-6.1-sol"
MODEL = "gpt-5"

PDF_DIR = Path("PATH TO PDFs") # ENTER YOUR PDF DIRECTORY
REVIEW_FILE = Path("reviews_api_gpt5.jsonl") # SAVE REVIEW PDF
CITATION_FILE = Path("review_citations_api_gpt5.jsonl") # SAVE WEAKNESS CITATION PDF


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


# Preserve original unsorted directory order.
pdf_files = [
    p for p in PDF_DIR.iterdir()
    if p.is_file() and p.suffix.lower() == ".pdf"
][0:200]

existing_reviews = load_records(REVIEW_FILE)
existing_citations = load_records(CITATION_FILE)


for pdf_path in tqdm(pdf_files, desc="Processing papers", unit="paper"):

    paper_id = pdf_path.stem

    if paper_id in existing_citations:
        continue

    try:
        # ============================================
        # STEP 1: GENERATE ICLR REVIEW
        # ============================================

        if paper_id in existing_reviews:
            review_record = existing_reviews[paper_id]
            review = review_record["generated_review"]
            response_id = review_record.get("response_id")

        else:
            with pdf_path.open("rb") as pdf:
                uploaded = client.files.create(
                    file=pdf,
                    purpose="user_data"
                )

            response = client.responses.create(
                model=MODEL,
                reasoning={"effort": "low"},
                input=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_id": uploaded.id,
                        },
                        {
                            "type": "input_text",
                            "text": (
                                f"I am reviewing this paper for ICLR. "
                                f"Give me an ICLR style review."
                            ),
                        },
                    ],
                }],
                tools=[],
                tool_choice="none",
                store=True,
            )

            review = response.output_text
            response_id = response.id

            review_record = {
                "paper_id": paper_id,
                "generated_review": review,
                "response_id": response_id,
                "file_id": uploaded.id,
            }

            save_record(REVIEW_FILE, review_record)
            existing_reviews[paper_id] = review_record

        # ============================================
        # STEP 2: IDENTIFY PAGE REFERENCES
        # ============================================

        citation_prompt = """You are given a research paper and an ICLR-style review that you previously generated for that paper.

Your task is to identify the section of the paper that each weakness in the review is directed at.

Definitions:
- Main body: everything before the References section.
- Appendix: everything after the References section.
- An item is directed at a section if that section contains the content the item praises or criticizes (a claim, method, result, table, figure, proof, or experimental detail).

Instructions:
1. Identify every distinct weakness in the review.
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

        if response_id:
            citation_response = client.responses.create(
                model=MODEL,
                reasoning={"effort": "low"},
                previous_response_id=response_id,
                input=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": citation_prompt,
                        }
                    ],
                }],
                tools=[],
                tool_choice="none",
            )

        else:
            # Fallback for reviews saved by the original
            # script without a response_id.
            with pdf_path.open("rb") as pdf:
                uploaded = client.files.create(
                    file=pdf,
                    purpose="user_data"
                )

            citation_response = client.responses.create(
                model=MODEL,
                reasoning={"effort": "low"},
                input=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_id": uploaded.id,
                        },
                        {
                            "type": "input_text",
                            "text": (
                                f"Original review:\n{review}\n\n"
                                f"{citation_prompt}"
                            ),
                        },
                    ],
                }],
                tools=[],
                tool_choice="none",
            )

        citation_record = {
            "paper_id": paper_id,
            "page_citations": citation_response.output_text,
        }

        save_record(CITATION_FILE, citation_record)
        existing_citations[paper_id] = citation_record

        cached = (
            citation_response.usage.input_tokens_details.cached_tokens
            if citation_response.usage else 0
        )

        tqdm.write(
            f"{paper_id}: citations saved "
            f"(cached input tokens: {cached})"
        )

    except Exception as error:
        tqdm.write(f"Failed: {pdf_path.name} — {error}")

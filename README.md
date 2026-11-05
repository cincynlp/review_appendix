# AI Reviews Are Biased Towards Weaknesses in the Appendix

Official repository for the paper:

> **AI Reviews Are Biased Towards Weaknesses in the Appendix**
> Paper: coming soon

---

## Overview

Large language models (LLMs) are increasingly used to draft conference-style reviews of research papers. This work studies which parts of a paper these reviews criticize. We generate 3,200 reviews for 800 papers from ACL 2026, EMNLP 2026, COLM 2026, and ICLR 2027 using four recent models, map every strength and weakness back to the section of the paper it refers to, and compare the result with human-written ICLR reviews from 2021 to 2024.

### Main Findings

- The latest GPT and Claude models draw 50% or more of their weaknesses from appendix-related content, either alone or together with the main body.
- The pattern holds across models, venues, and access methods (API, coding agent, and web interface).
- Human reviewers draw 8.4% of their weaknesses from the appendix, compared with more than 61% for LLM reviewers on ICLR 2027 papers.
- Appendix criticism from LLMs concentrates on technical details, such as cross-checking numbers and requesting additional experiments, while human criticism of the appendix concerns the paper's framing and presentation.
- The behavior is largely absent in an earlier model generation (GPT-5).

---

## Method

The pipeline has three stages.

1. **Paper collection.** For each venue, 200 papers with an appendix of at least 15 pages are sampled. Author names, affiliations, email addresses, and links are removed from the PDFs, and web search is disabled for all model runs.
2. **Review generation.** Each model receives the anonymized PDF and a short prompt:
   `I am reviewing this paper for [conference_name]. Give me a [conference_name] style review.`
3. **Section attribution.** The same model receives the paper and its generated review, and names the section (at most two) that each strength and weakness is directed at. Each weakness is then assigned to one of three groups: main body only, both main body and appendix, or appendix only.

A separate analysis applies stage 3 to human reviews of ICLR 2021 to 2024 papers and categorizes appendix-related weaknesses into eight categories (Core Idea, Methodology, Results, Analysis, Prompts, Discussion, Tables and Figures, Presentation).

### Models

| Provider | Models |
|---|---|
| OpenAI | GPT-6-Astra, GPT-6.1-Sol (GPT-5 for the generation comparison) |
| Anthropic | Claude-Opus-5.5, Claude-Sonnet-5.5 |

### Venues

| Venue | Paper type | Papers per split |
|---|---|---|
| ACL 2026 (main) | Camera-ready, accepted | 100 + 100 |
| EMNLP 2026 (main) | Camera-ready, accepted | 100 + 100 |
| COLM 2026 | Camera-ready, accepted | 100 + 100 |
| ICLR 2027 | Anonymous submissions | 100 + 100 |

---

## Repository Structure

```
review_appendix/
│
├── data/
│   ├── gpt_review/                 # GPT-generated reviews
│   ├── gpt_citation/               # GPT section attributions for each review
│   ├── claude_review/              # Claude-generated reviews
│   ├── claude_citation/            # Claude section attributions for each review
│   └── human_review_iclr/          # ICLR 2021-2024 human review analysis and ICLR 2027 LLM categories
│
├── scripts/
│   ├── download_papers/            # Paper sampling, download, and appendix-length filtering
│   ├── llm_review/                 # Review generation, section attribution, and result aggregation
│   └── human_review/               # Human review download and analysis
│
├── LICENSE
└── README.md
```

### Data File Naming

Review and attribution files are named by model run, venue, and paper split:

```
reviews_<run>_<venue>_<split>.jsonl
citations_<run>_<venue>_<split>.jsonl
```

- `<venue>` is one of `acl_main`, `emnlp`, `colm`, `iclr`.
- `<split>` is `100` (papers 1 to 100) or `100_200` (papers 101 to 200).
- Claude runs use `claude_opus55` and `claude_sonnet55`. GPT run names encode the access method (`A_agentic` for the coding agent, `B_fulltext` for extracted full text, `D_web` for the web interface), the model (`astra`, `sol`), and the reasoning effort.

### Record Format

Review record (`reviews_*.jsonl`):

```json
{"paper_id": "<id>", "generated_review": "<full review text>"}
```

Section attribution record (`citations_*.jsonl`):

```json
{"paper_id": "<id>", "page_citations": "S1 - Section 3 - Methodology\nW1 - Section 4 - Results; Appendix C - Additional Results\n..."}
```

`S` items are strengths and `W` items are weaknesses. Each item lists one or two sections, or `not found`. Sections before the References are the main body; sections after it are the appendix.

---

## Installation

Python 3.10 or later is required.

```bash
git clone https://github.com/cincynlp/review_appendix.git
cd review_appendix
pip install openai openreview-py pymupdf tqdm
```

Depending on the workflow, you also need:

- an OpenAI API key for `scripts/llm_review/review_api.py`, set inside the script
- the Codex CLI for `scripts/llm_review/review_codex.py`
- the Claude Code CLI (`claude`) for `scripts/llm_review/review_claude.py`
- an OpenReview account for the ICLR download scripts

---

## Usage

### 1. Collect and filter papers

```bash
python scripts/download_papers/download_papers_iclr.py
python scripts/download_papers/download_papers_colm.py
python scripts/download_papers/download_long_paper_acl_emnlp.py
python scripts/download_papers/extract_long_appendix_iclr.py
python scripts/download_papers/extract_appendix_colm.py
```

These scripts sample papers, download the PDFs, and keep papers whose appendix meets the minimum length. Paths and sample sizes are set at the top of each script.

### 2. Generate reviews and section attributions

```bash
# Claude (Opus or Sonnet), through Claude Code in headless mode
python scripts/llm_review/review_claude.py --venue ICLR --model claude-opus-5-5 \
    --tag opus55 --pdf-dir <anonymized_pdfs> --ids-file <ids.txt>

# GPT, through the Codex CLI
python scripts/llm_review/review_codex.py

# GPT, through the API
python scripts/llm_review/review_api.py
```

Each script writes one review file and one section attribution file, and can be resumed after an interruption.

### 3. Compute weakness locations

```bash
python scripts/llm_review/weakness_location_result.py <citations_file>.jsonl
```

This reports the share of weaknesses from the main body only, both, and the appendix only.

### 4. Human review analysis

```bash
python scripts/human_review/download_iclr_2021_23.py
python scripts/human_review/download_iclr_2024_26.py
python scripts/human_review/citation_iclr_human_review.py
python scripts/human_review/weakness_location_result.py <citations_file>.jsonl
```

---

## Data Availability

Paper PDFs are not included because of their size and because ICLR 2027 submissions are not public. The paper identifiers in each data file correspond to OpenReview submission IDs (ICLR, COLM) and ACL Anthology IDs (ACL, EMNLP), which can be used to retrieve the papers. Generated reviews were produced only to study model behavior and were not shared with authors or used in any real review process.

---

## Citation

The paper is not yet published. Citation information will be added here.

```bibtex
@article{ai_reviews_appendix,
  title  = {AI Reviews Are Biased Towards Weaknesses in the Appendix},
  note   = {Coming soon}
}
```

---

## License

This repository is released under the MIT License. See [LICENSE](LICENSE).

---

## Contact

For questions about the data, code, or paper, please open a [GitHub Issue](https://github.com/cincynlp/review_appendix/issues).

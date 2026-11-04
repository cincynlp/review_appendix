import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ==========================
# CONFIGURATION
# ==========================

INPUT_FILE = Path(
    sys.argv[1]
    if len(sys.argv) > 1
    else "iclr_2021_2026/human_review_citation_iclr_gptsol.jsonl"
)

OUTPUT_FIELD = "page_citations"

YEARS = range(2021, 2027)

# ==========================
# REGEX
# ==========================

ITEM_RE = re.compile(
    r"^\W*([SW])\s*(\d+)\W*?\s*[-–—:]\s*(.+)$"
)

APPENDIX_RE = re.compile(
    r"^(?:appendix|appendices|app\.|supplementary)\b"
    r"|^[A-Z](?:\.\d+)*\s*(?:[-–—:]|$)",
    re.I,
)

# ==========================
# PARSING FUNCTIONS
# ==========================

def section_kind(section):

    section = section.strip().strip("*").strip()

    if not section or section.lower().startswith("not found"):
        return None

    return (
        "appendix"
        if APPENDIX_RE.match(section)
        else "main_body"
    )


def parse_output(text):

    labels = {}

    for line in text.splitlines():

        match = ITEM_RE.match(line.strip())

        if not match:
            continue

        kind, number, value = match.groups()

        kinds = {
            k
            for k in (
                section_kind(part)
                for part in value.split(";")
            )
            if k
        }

        if kinds == {"main_body"}:
            label = "main_body_only"

        elif kinds == {"appendix"}:
            label = "appendix_only"

        elif kinds:
            label = "both"

        else:
            label = "not_found"

        labels.setdefault(
            (kind.upper(), int(number)),
            label
        )

    return labels

# ==========================
# PRINT FUNCTION
# ==========================

def print_table(year, title, counts):

    located = (
        counts["main_body_only"]
        + counts["both"]
        + counts["appendix_only"]
    )

    total = located + counts["not_found"]

    print(f"\nICLR {year} — {title}")
    print(f"Total items: {total} | Located: {located} | Not found: {counts['not_found']}")

    for label in (
        "main_body_only",
        "both",
        "appendix_only"
    ):

        percentage = (
            100 * counts[label] / located
            if located else 0.0
        )

        print(
            f"  {label:<16} "
            f"{counts[label]:>5} "
            f"{percentage:>7.2f}%"
        )


# ==========================
# LOAD DATA
# ==========================

with INPUT_FILE.open(encoding="utf-8") as source:

    records = [
        json.loads(line)
        for line in source
        if line.strip()
    ]

# Keep the latest record for each unique review
latest = {}

for record in records:

    key = (
        int(record["year"]),
        record["paper_id"],
        record.get("review_id")
    )

    latest[key] = record

# ==========================
# GROUP BY YEAR
# ==========================

strengths_by_year = defaultdict(Counter)
weaknesses_by_year = defaultdict(Counter)

papers_by_year = defaultdict(set)
reviews_by_year = Counter()

unparsed = []

for (year, paper_id, review_id), record in latest.items():

    labels = parse_output(
        record.get(OUTPUT_FIELD) or ""
    )

    if not labels:
        unparsed.append((year, paper_id, review_id))
        continue

    papers_by_year[year].add(paper_id)
    reviews_by_year[year] += 1

    for (kind, _), label in labels.items():

        if kind == "S":
            strengths_by_year[year][label] += 1

        else:
            weaknesses_by_year[year][label] += 1


# ==========================
# PRINT RESULTS BY YEAR
# ==========================

print("\n==============================================")
print("ICLR HUMAN REVIEW SECTION ATTRIBUTION")
print("==============================================")

print(
    "\nPercentages are calculated over located items."
    "\n'Not found' items are excluded from percentages."
)

for year in YEARS:

    print("\n" + "=" * 50)

    print(f"ICLR {year}")

    print(f"Papers: {len(papers_by_year[year])}")
    print(f"Reviews: {reviews_by_year[year]}")

    print_table(
        year,
        "STRENGTHS",
        strengths_by_year[year]
    )

    print_table(
        year,
        "WEAKNESSES",
        weaknesses_by_year[year]
    )


# ==========================
# UNPARSED RECORDS
# ==========================

if unparsed:

    print(
        f"\nNo S/W lines could be parsed "
        f"for {len(unparsed)} reviews."
    )

    for year, paper_id, review_id in unparsed[:10]:

        print(
            f"  {year} | {paper_id} | {review_id}"
        )
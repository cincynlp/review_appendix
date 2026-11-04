import csv
import re
import shutil
from collections import Counter
from pathlib import Path

import fitz  # pip install pymupdf

SOURCE = Path("pdfs")
DESTINATION = Path("pdf_app")
MIN_PAGES = 15

# Matches: A Proofs, A. Proofs, A.1 Proof of Theorem 1
LETTERED = re.compile(
    r"^([A-Z])(?:\.\d+)*\.?\s+([A-Z][^\n]{2,110})$"
)

EXPLICIT = re.compile(
    r"^(?:Appendix|Appendices|Supplementary Material)"
    r"(?:\s|:|$)",
    re.IGNORECASE,
)


def extract_lines(document):
    """Extract text lines, typography, and approximate body font size."""
    pages = []
    font_counts = Counter()

    for page in document:
        lines = []

        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue

            for line in block.get("lines", []):
                spans = [
                    span for span in line["spans"]
                    if span["text"].strip()
                ]
                if not spans:
                    continue

                text = " ".join(
                    span["text"].strip() for span in spans
                )
                text = re.sub(r"\s+", " ", text).strip()

                size = max(span["size"] for span in spans)
                bold = any(
                    span["flags"] & 16
                    or "bold" in span["font"].lower()
                    for span in spans
                )

                for span in spans:
                    font_counts[round(span["size"], 1)] += len(span["text"])

                lines.append({
                    "text": text,
                    "size": size,
                    "bold": bold,
                    "y": line["bbox"][1],
                    "height": page.rect.height,
                })

        pages.append(lines)

    body_size = font_counts.most_common(1)[0][0] if font_counts else 10
    return pages, body_size


def detect_appendix(document):
    """Return (zero-based start page, evidence), or (None, reason)."""

    # First try PDF bookmarks.
    for level, title, page_number in document.get_toc():
        title = title.strip()
        match = LETTERED.match(title)

        if page_number >= 3 and (
            EXPLICIT.match(title)
            or (match and match.group(1) == "A")
        ):
            return page_number - 1, f"PDF bookmark: {title}"

    pages, body_size = extract_lines(document)

    # Repeated text near the top is likely a running header.
    header_counts = Counter(
        text
        for lines in pages
        for text in {
            line["text"]
            for line in lines
            if line["y"] < line["height"] * 0.10
        }
    )

    candidates = []

    for page_index, lines in enumerate(pages):
        if page_index < 2:
            continue

        # Also handle headings split into a standalone "A" and a title.
        combined = []
        for index, line in enumerate(lines):
            combined.append(line)

            if (
                re.fullmatch(r"[A-Z]\.?", line["text"])
                and index + 1 < len(lines)
            ):
                following = lines[index + 1]
                if 0 <= following["y"] - line["y"] < 35:
                    combined.append({
                        **line,
                        "text": line["text"] + " " + following["text"],
                        "bold": line["bold"] or following["bold"],
                        "size": max(line["size"], following["size"]),
                    })

        for line in combined:
            text = line["text"]

            if len(text) > 120 or re.search(r"\.{3,}", text):
                continue

            if (
                line["y"] < line["height"] * 0.10
                and header_counts[text] >= 3
            ):
                continue

            # Require heading-like typography.
            if not (line["bold"] or line["size"] >= body_size + 0.5):
                continue

            if EXPLICIT.match(text):
                candidates.append((page_index, "explicit", text))
                continue

            match = LETTERED.match(text)
            if match:
                candidates.append((page_index, match.group(1), text))

    # Prefer A followed by another lettered section:
    # this reduces false positives such as "A New Method" in body text.
    for page_index, letter, title in candidates:
        if letter == "A":
            has_following_section = any(
                later_page >= page_index and later_letter in {"B", "C"}
                for later_page, later_letter, _ in candidates
            )
            if has_following_section:
                return page_index, f"Lettered section sequence: {title}"

    for page_index, letter, title in candidates:
        if letter == "explicit":
            return page_index, f"Formatted appendix heading: {title}"

    return None, "Uncertain: no reliable appendix boundary detected"


def main():
    if not SOURCE.is_dir():
        raise FileNotFoundError(f"Folder not found: {SOURCE.resolve()}")

    DESTINATION.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(
        path for path in SOURCE.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )

    rows = []
    copied = 0

    for index, path in enumerate(pdfs, start=1):
        row = {"filename": path.name}

        try:
            with fitz.open(path) as document:
                total = len(document)
                start, evidence = detect_appendix(document)

            pages = total - start if start is not None else None

            row.update({
                "total_pages": total,
                "appendix_start": start + 1 if start is not None else "",
                "estimated_appendix_pages": pages if pages is not None else "",
                "evidence": evidence,
            })

            if pages is None:
                row["status"] = "Needs inspection"
            elif pages >= MIN_PAGES:
                shutil.copy2(path, DESTINATION / path.name)
                copied += 1
                row["status"] = "Copied"
            else:
                row["status"] = "Below threshold"

        except Exception as exc:
            row.update(status="Error", evidence=str(exc))

        rows.append(row)
        print(f"[{index}/{len(pdfs)}] {path.name}: {row['status']}")

    report = DESTINATION / "appendix_report.csv"
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "filename", "total_pages", "appendix_start",
            "estimated_appendix_pages", "status", "evidence",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nCopied {copied} PDFs to {DESTINATION.resolve()}")
    print(f"Detection report: {report.resolve()}")


if __name__ == "__main__":
    main()
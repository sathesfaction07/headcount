"""Loading documents, and deciding what a file actually is.

Format detection is by CONTENT, not by extension. Email attachments are misnamed constantly
-- a spreadsheet saved as `.txt`, a CSV called `.xls`, an HTML page emailed as `.doc` -- and
trusting the extension means handing a zip archive to a CSV parser and reporting zero codes
from a file that was full of them. A zero is indistinguishable from "this source had none".
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from pydantic import BaseModel

from multi_extract.models import Document

# Magic byte prefixes, longest first so a more specific signature wins. These are the ones
# that actually arrive as attachments and get misnamed.
SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),          # also .xlsx and .docx, which are zip containers
    (b"\xd0\xcf\x11\xe0", "ole"),    # legacy .xls and .doc
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
]


class Detection(BaseModel):
    """What a file appears to be, and whether that matches its name."""

    path: str
    detected: str
    extension: str
    readable_as_text: bool
    mismatched: bool

    def describe(self) -> str:
        if not self.mismatched:
            return f"{self.path}: {self.detected}, consistent with its extension"
        return (
            f"{self.path}: content is {self.detected} but the extension says "
            f"{self.extension or '(none)'}. Parsing it by extension would have "
            f"{'produced nothing' if not self.readable_as_text else 'produced garbage'}."
        )


def detect(path: Path) -> Detection:
    """Identify a file by its first bytes, then by whether it decodes as text.

    Binary signatures are checked before any text heuristic, because a zip container can
    contain a run of bytes that happens to decode -- so asking "is it text" first would
    misclassify an .xlsx as a text file and extract nothing from it.
    """
    head = path.open("rb").read(2048)
    extension = path.suffix.lower().lstrip(".")

    detected = "unknown"
    for signature, name in SIGNATURES:
        if head.startswith(signature):
            detected = name
            break

    readable = False
    if detected == "unknown":
        try:
            text = head.decode("utf-8")
        except UnicodeDecodeError:
            detected = "binary"
        else:
            readable = True
            stripped = text.lstrip()
            if stripped.startswith(("<!DOCTYPE", "<html", "<?xml", "<")):
                detected = "markup"
            elif stripped.startswith(("{", "[")):
                detected = "json"
            elif _looks_like_delimited(text):
                detected = "delimited"
            else:
                detected = "text"

    # A zip container is not itself a mismatch when the name admits it.
    consistent = {
        "pdf": {"pdf"},
        "zip": {"zip", "xlsx", "docx", "odt", "ods"},
        "ole": {"xls", "doc", "ppt"},
        "png": {"png"},
        "jpeg": {"jpg", "jpeg"},
        "markup": {"html", "htm", "xml", "xhtml"},
        "json": {"json", "jsonl", "ndjson"},
        "delimited": {"csv", "tsv", "txt"},
        "text": {"txt", "text", "log", "md", ""},
        "binary": set(),
    }.get(detected, set())

    return Detection(
        path=path.name,
        detected=detected,
        extension=extension,
        readable_as_text=readable,
        mismatched=extension not in consistent,
    )


def _looks_like_delimited(text: str) -> bool:
    """Whether the first lines share a consistent delimiter count.

    Requires at least two lines agreeing. One line with commas in it is a sentence; two lines
    with the same number of commas is a table. Without the agreement check, any prose
    containing a comma would be parsed as a single-row CSV.
    """
    lines = [line for line in text.splitlines() if line.strip()][:5]
    if len(lines) < 2:
        return False
    for delimiter in (",", "\t", ";", "|"):
        counts = {line.count(delimiter) for line in lines}
        if len(counts) == 1 and counts != {0}:
            return True
    return False


def load_documents(path: Path) -> tuple[list[Document], list[str]]:
    """Load documents from a JSON Lines manifest.

    Each line is one document. A manifest rather than a directory walk, because pagination
    metadata (`page`, `declared_total`) cannot be recovered from a filename and is the whole
    basis of the completeness accounting.

    Returns `(documents, problems)`. One malformed line should not cost the other thousand,
    and a line that cannot be read is named rather than skipped silently.
    """
    if not path.exists():
        raise FileNotFoundError(f"no such manifest: {path}")

    documents: list[Document] = []
    problems: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            documents.append(Document.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as error:
            problems.append(f"{path.name}:{number}: {error}")
    return documents, problems


def load_truth(path: Path) -> set[str]:
    """Load the known-correct code set.

    A plain newline- or CSV-delimited list. Deliberately NOT derived from running the
    extractor over the corpus: a truth set produced by the thing being measured makes recall
    100% by construction and the measurement meaningless.
    """
    if not path.exists():
        raise FileNotFoundError(f"no such truth file: {path}")
    text = path.read_text(encoding="utf-8-sig")
    if "," in text.splitlines()[0]:
        reader = csv.DictReader(text.splitlines())
        column = "code" if "code" in (reader.fieldnames or []) else (reader.fieldnames or [""])[0]
        return {row[column].strip().upper() for row in reader if row.get(column, "").strip()}
    return {line.strip().upper() for line in text.splitlines() if line.strip()}

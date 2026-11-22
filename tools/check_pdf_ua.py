# License: AGPL-3.0-only (LICENSE).
"""Validate CGM Report PDFs against PDF/UA-1 (ISO 14289-1) with veraPDF, when veraPDF is installed.

veraPDF (https://verapdf.org/software/) is the open-source PDF/UA and PDF/A validator; it needs Java and is not a
dependency of GlucoCraft or of its tests. tests/test_pdf_tagging.py checks the structure without it; this script runs
veraPDF's whole PDF/UA-1 profile for the checks only veraPDF makes.

Usage, from the repository root:
    python tools/check_pdf_ua.py                 the reports of the test suite (14-day summary, 30-day full report,
                                                 90-day summary), rendered into a temporary folder
    python tools/check_pdf_ua.py report.pdf ...  the given PDF files

veraPDF is found by the environment variable VERAPDF (the path of its command-line launcher), else as "verapdf" on the
PATH. Exit status: 0 when every file is compliant, 1 when a file is not, 2 when veraPDF is not installed.

The CGM Report states its PDF/UA identification in its metadata (pdfuaid:part 1, pdf_tags.py, written since
veraPDF passed the reports); veraPDF is still asked for the PDF/UA-1 profile explicitly (--flavour ua1).
A file passes when no rule fails.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def find_verapdf() -> str | None:
    """Return the veraPDF launcher: $VERAPDF, else "verapdf" (or verapdf.bat on Windows) on the PATH."""
    configured = os.environ.get("VERAPDF")
    if configured:
        return configured if Path(configured).is_file() else None
    return shutil.which("verapdf") or shutil.which("verapdf.bat")


def render_test_reports(folder: Path) -> list[Path]:
    """Render the reports of tests/test_i18n_english_output.py into `folder` and return their paths."""
    sys.path.insert(0, str(REPO / "tests"))
    # the test set-up first: it installs the Home Assistant stand-ins and puts the integration on the path
    importlib.import_module("conftest")
    from glucocraft import report_pdf

    from test_i18n_english_output import _report_cases

    paths = []
    for name, data, full, color in _report_cases():
        pdf, _pages = report_pdf.render(data, color, full)
        path = folder / ("cgm-report_" + "".join(ch if ch.isalnum() else "-" for ch in name) + ".pdf")
        path.write_bytes(pdf)
        paths.append(path)
    return paths


def results(mrr: str) -> list[tuple[str, bool, list[str]]]:
    """Read veraPDF's machine-readable report: per file, its name, whether it passes, and its failed rules ("clause-test
    (n checks): description"). A file passes when veraPDF read it and no rule failed. An unreadable report raises
    ET.ParseError."""
    out = []
    for job in ET.fromstring(mrr).iter("job"):
        item = job.find("item/name")
        failed = []
        for rule in job.iter("rule"):
            if rule.get("status") == "failed":
                description = (rule.findtext("description") or "").strip()
                text = (
                    f"{rule.get('clause')}-{rule.get('testNumber')} ({rule.get('failedChecks')} checks): {description}"
                )
                failed.append(text)
        report = job.find("validationReport")
        ok = report is not None and not failed
        out.append((item.text if item is not None else "?", ok, failed))
    return out


def validate(verapdf: str, files: list[Path]) -> bool:
    """Run veraPDF's PDF/UA-1 profile on the files, print each file's result and failed rules; True when all pass."""
    run = subprocess.run(
        [verapdf, "--flavour", "ua1", "--format", "mrr", *map(str, files)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        found = results(run.stdout)
    except ET.ParseError:
        print(run.stdout or run.stderr)
        return False
    if not found:
        print(run.stdout or run.stderr)
        return False
    for name, ok, failed in found:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        for text in failed:
            print(f"      {text}")
    return all(ok for _, ok, _ in found)


def main(argv: list[str]) -> int:
    verapdf = find_verapdf()
    if verapdf is None:
        print("veraPDF is not installed (https://verapdf.org/software/); set VERAPDF or put verapdf on the PATH.")
        return 2
    if argv:
        return 0 if validate(verapdf, [Path(a) for a in argv]) else 1
    with tempfile.TemporaryDirectory() as tmp:
        return 0 if validate(verapdf, render_test_reports(Path(tmp))) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

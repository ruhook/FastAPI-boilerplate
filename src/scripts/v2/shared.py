from __future__ import annotations

import json
import base64
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx


CURRENT_FILE = Path(__file__).resolve()
HR_SERVER_ROOT = CURRENT_FILE.parents[3]
REPO_ROOT = HR_SERVER_ROOT.parent
TMP_DIR = HR_SERVER_ROOT / "tmp" / "v2"

DEFAULT_WEB_BASE_URL = "http://127.0.0.1:8000/api/v1"
DEFAULT_ADMIN_BASE_URL = "http://127.0.0.1:8001/api"

DEFAULT_PORTAL_CANDIDATE_EMAIL = "712696307@qq.com"
DEFAULT_PORTAL_CANDIDATE_PASSWORD = "12345678"
DEFAULT_PROGRESS_CANDIDATE_EMAIL = "progress.v2.manual@example.com"
DEFAULT_PROGRESS_CANDIDATE_PASSWORD = "Candidate123!"
DEFAULT_TIMESHEET_CANDIDATE_EMAIL = "timesheet.viewer@example.com"
DEFAULT_TIMESHEET_CANDIDATE_PASSWORD = "Candidate123!"

DEFAULT_FLOW_ADMIN_USERNAME = "flowadmin"
DEFAULT_FLOW_ADMIN_PASSWORD = "FlowAdmin123!"
DEFAULT_TIMESHEET_ADMIN_USERNAME = "timesheetadmin"
DEFAULT_TIMESHEET_ADMIN_PASSWORD = "TimesheetAdmin123!"
DEFAULT_SUPER_ADMIN_USERNAME = "admin"
DEFAULT_SUPER_ADMIN_PASSWORD = "12345678"
DEFAULT_ASSESSMENT_REVIEWER_USERNAME = "judgereviewer"
DEFAULT_ASSESSMENT_REVIEWER_PASSWORD = "JudgeReview123!"

EXPECTED_REFERRAL_MILESTONES = [
    {"required_hours": "40.00", "reward_amount": "25.00"},
    {"required_hours": "100.00", "reward_amount": "75.00"},
    {"required_hours": "180.00", "reward_amount": "150.00"},
    {"required_hours": "300.00", "reward_amount": "300.00"},
]


@dataclass
class ModuleRunResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    log_path: str


def ensure_tmp_dir() -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR


def timestamp_tag() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def print_step(title: str) -> None:
    print(f"\n=== {title} ===")


def print_detail(message: str) -> None:
    print(f"  - {message}")


def quantize_decimal(value: Decimal | str | int | float | None) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ensure_status(response: httpx.Response, message: str) -> dict:
    if response.status_code >= 400:
        raise RuntimeError(f"{message}: {response.status_code} {response.text}")
    return response.json()


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def find_latest_seed_summary_path() -> Path | None:
    candidates = sorted(TMP_DIR.glob("manual-review-seed-v2-*.json"))
    if not candidates:
        return None
    return candidates[-1]


def load_latest_seed_summary() -> dict[str, object]:
    path = find_latest_seed_summary_path()
    if path is None:
        raise RuntimeError("No manual-review V2 seed summary found. Run src.scripts.v2.seed_manual_review_data first.")
    return json.loads(path.read_text(encoding="utf-8"))


def run_module(
    module: str,
    *args: str,
    check: bool = True,
    log_prefix: str | None = None,
) -> ModuleRunResult:
    ensure_tmp_dir()
    command = [sys.executable, "-m", module, *args]
    completed = subprocess.run(
        command,
        cwd=HR_SERVER_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    log_name = log_prefix or module.rsplit(".", 1)[-1]
    log_path = TMP_DIR / f"{timestamp_tag()}-{log_name}.log"
    log_path.write_text(
        "\n".join(
            [
                f"$ {' '.join(command)}",
                "",
                completed.stdout.rstrip(),
                "",
                "[stderr]",
                completed.stderr.rstrip(),
            ]
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"log: {log_path}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return ModuleRunResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        log_path=str(log_path),
    )


def extract_trailing_json(text: str) -> dict | list:
    lines = [line for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not line.lstrip().startswith(("{", "[")):
            continue
        candidate = "\n".join(lines[index:])
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("Unable to extract trailing JSON payload from script output.")


def parse_portal_demo_log(text: str) -> dict[str, object]:
    summary: dict[str, object] = {
        "my_jobs_summary": [],
        "my_contracts_summary": [],
    }
    fresh_title = re.search(r"fresh job title:\s*(.+)", text)
    fresh_job_id = re.search(r"fresh job id:\s*(\d+)", text)
    candidate_email = re.search(r"candidate email:\s*(.+)", text)
    candidate_password = re.search(r"candidate password:\s*(.+)", text)
    if fresh_title:
        summary["fresh_job_title"] = fresh_title.group(1).strip()
    if fresh_job_id:
        summary["fresh_job_id"] = int(fresh_job_id.group(1))
    if candidate_email:
        summary["candidate_email"] = candidate_email.group(1).strip()
    if candidate_password:
        summary["candidate_password"] = candidate_password.group(1).strip()

    current_section: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "my jobs summary:":
            current_section = "my_jobs_summary"
            continue
        if stripped == "my contracts summary:":
            current_section = "my_contracts_summary"
            continue
        if current_section and stripped.startswith("- "):
            summary[current_section].append(stripped[2:].strip())
        elif current_section and stripped and not stripped.startswith("- "):
            current_section = None
    return summary


async def preflight_http_endpoint(url: str) -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        if response.status_code >= 400:
            raise RuntimeError(f"Preflight failed for {url}: {response.status_code} {response.text}")


async def login_candidate(
    client: httpx.AsyncClient,
    *,
    email: str,
    password: str,
) -> str:
    response = await client.post(
        "/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    payload = ensure_status(response, f"Candidate login failed for {email}")
    return str(payload["access_token"])


async def login_admin(
    client: httpx.AsyncClient,
    *,
    username_or_email: str,
    password: str,
) -> str:
    response = await client.post(
        "/v1/auth/login",
        json={"username_or_email": username_or_email, "password": password},
    )
    payload = ensure_status(response, f"Admin login failed for {username_or_email}")
    return str(payload["access_token"])


def build_minimal_docx_bytes(text: str = "V2 contract upload smoke test") -> bytes:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>"
        f"{text}"
        "</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def build_minimal_pdf_bytes(text: str = "V2 PDF smoke test") -> bytes:
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content_stream = f"BT /F1 14 Tf 50 780 Td ({safe_text}) Tj ET".encode("latin-1", errors="replace")

    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        ),
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(content_stream)} >> stream\n".encode("ascii")
        + content_stream
        + b"\nendstream endobj\n",
    ]

    header = b"%PDF-1.4\n"
    body = bytearray(header)
    offsets = [0]
    for obj in objects:
        offsets.append(len(body))
        body.extend(obj)

    xref_offset = len(body)
    body.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    body.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(body)


def build_minimal_png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX9kAAAAASUVORK5CYII="
    )


def _xlsx_column_name(index: int) -> str:
    if index < 1:
        raise ValueError("Excel columns are 1-based.")
    name = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        name = chr(65 + remainder) + name
    return name


def build_minimal_xlsx_bytes(
    value: str = "V2 assessment upload smoke test",
    *,
    rows: list[list[str]] | None = None,
) -> bytes:
    rendered_rows = rows or [[value]]
    sheet_rows: list[str] = []
    for row_index, row_values in enumerate(rendered_rows, start=1):
        cells: list[str] = []
        for column_index, cell_value in enumerate(row_values, start=1):
            cell_ref = f"{_xlsx_column_name(column_index)}{row_index}"
            cells.append(
                f'<c r="{cell_ref}" t="inlineStr"><is><t>{escape(str(cell_value))}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>",
        )
    return buffer.getvalue()

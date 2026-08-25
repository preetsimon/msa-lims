"""Certificate of Analysis PDF rendering.

Pure: takes an already-assembled :class:`CertificateContent`, returns bytes.
Nothing here touches a session or a clock — every fact printed on the page,
including the issue date, arrives as data.

**Byte-deterministic.** Rendering the same content twice produces
byte-identical output — verified directly by a property test, not assumed —
which is why the stored ``pdf_sha256`` is a real content hash of the document
rather than a label nobody checks. Two things make this true:

* ``reportlab.pdfgen.canvas.Canvas(..., invariant=1)`` disables the library's
  default behaviour of stamping a fresh, effectively-random document ID into
  the PDF trailer on every render.
* Standard 14 fonts (Helvetica) are used throughout. They are part of the PDF
  specification itself, never embedded, so there is no font-substitution or
  embedding step whose output could vary by platform or library version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 0.75 * inch

#: Long free text is wrapped at the printable width rather than drawn as one
#: line: a supersession reason or a note that runs past the right edge is
#: content silently missing from a signed document.
_TEXT_FONT = "Helvetica-Oblique"
_TEXT_SIZE = 9
_TEXT_LEADING = 0.14 * inch


def _wrap_lines(c: canvas.Canvas, text: str, max_width: float) -> list[str]:
    """Break ``text`` into lines that fit ``max_width`` in the text font.

    Uses :meth:`stringWidth` on the standard 14 fonts, which are part of the
    PDF spec itself — so the wrapping is exact and byte-deterministic, not an
    average-character-width guess.
    """
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if current and c.stringWidth(candidate, _TEXT_FONT, _TEXT_SIZE) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True, slots=True)
class CertifiedSample:
    sample_id: str
    method: str
    au_display: str


@dataclass(frozen=True, slots=True)
class CertificateContent:
    certificate_number: str
    client_name: str
    issued_at: datetime
    issued_by_name: str
    samples: tuple[CertifiedSample, ...]
    supersedes_number: str | None = None
    superseded_reason: str | None = None
    notes: str | None = None


def render_pdf(content: CertificateContent) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    c.setTitle(f"Certificate of Analysis {content.certificate_number}")
    c.setAuthor("MSA LIMS")
    c.setSubject(f"Certificate of Analysis for {content.client_name}")

    y = PAGE_HEIGHT - MARGIN
    left = MARGIN
    right = PAGE_WIDTH - MARGIN

    c.setFont("Helvetica-Bold", 16)
    c.drawString(left, y, "Certificate of Analysis")
    c.setFont("Helvetica", 10)
    c.drawRightString(right, y, content.certificate_number)
    y -= 0.35 * inch

    c.setStrokeColorRGB(0, 0, 0)
    c.line(left, y, right, y)
    y -= 0.3 * inch

    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Client:")
    c.setFont("Helvetica", 10)
    c.drawString(left + 0.9 * inch, y, content.client_name)
    y -= 0.22 * inch

    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Issued:")
    c.setFont("Helvetica", 10)
    c.drawString(left + 0.9 * inch, y, content.issued_at.strftime("%Y-%m-%d %H:%M %Z").strip())
    y -= 0.22 * inch

    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Issued by:")
    c.setFont("Helvetica", 10)
    c.drawString(left + 0.9 * inch, y, content.issued_by_name)
    y -= 0.35 * inch

    if content.supersedes_number:
        c.setFont(_TEXT_FONT, _TEXT_SIZE)
        statement = (
            f"This certificate supersedes {content.supersedes_number}. "
            f"Reason: {content.superseded_reason}"
        )
        for line in _wrap_lines(c, statement, right - left):
            c.drawString(left, y, line)
            y -= _TEXT_LEADING
        y -= 0.16 * inch

    # -- results table --------------------------------------------------
    col_sample = left
    col_method = left + 2.6 * inch
    col_au = left + 4.6 * inch

    def draw_table_header() -> None:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(col_sample, y, "Sample")
        c.drawString(col_method, y, "Method")
        c.drawString(col_au, y, "Au")

    def draw_header_rule() -> None:
        c.setFont("Helvetica", 9)
        y_rule = y - 0.05 * inch
        c.line(left, y_rule, right, y_rule)

    draw_table_header()
    draw_header_rule()
    y -= 0.25 * inch

    for sample in content.samples:
        if y < MARGIN + 0.5 * inch:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = PAGE_HEIGHT - MARGIN
            # A continuation page carries the same column headings as the
            # first: a table that loses its header mid-document forces the
            # reader to flip back to interpret a column of numbers.
            draw_table_header()
            draw_header_rule()
            y -= 0.25 * inch
        c.setFont("Helvetica", 9)
        c.drawString(col_sample, y, sample.sample_id)
        c.drawString(col_method, y, sample.method)
        c.drawString(col_au, y, sample.au_display)
        y -= 0.2 * inch

    if content.notes:
        if y < MARGIN + _TEXT_LEADING:
            c.showPage()
            y = PAGE_HEIGHT - MARGIN
        c.setFont(_TEXT_FONT, _TEXT_SIZE)
        for line in _wrap_lines(c, f"Notes: {content.notes}", right - left):
            c.drawString(left, y, line)
            y -= _TEXT_LEADING

    c.showPage()
    c.save()
    return buf.getvalue()

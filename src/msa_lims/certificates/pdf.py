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
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(
            left,
            y,
            f"This certificate supersedes {content.supersedes_number}. "
            f"Reason: {content.superseded_reason}",
        )
        y -= 0.3 * inch

    # -- results table --------------------------------------------------
    col_sample = left
    col_method = left + 2.6 * inch
    col_au = left + 4.6 * inch

    c.setFont("Helvetica-Bold", 9)
    c.drawString(col_sample, y, "Sample")
    c.drawString(col_method, y, "Method")
    c.drawString(col_au, y, "Au")
    y -= 0.05 * inch
    c.line(left, y, right, y)
    y -= 0.2 * inch

    c.setFont("Helvetica", 9)
    for sample in content.samples:
        if y < MARGIN + 0.5 * inch:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = PAGE_HEIGHT - MARGIN
        c.drawString(col_sample, y, sample.sample_id)
        c.drawString(col_method, y, sample.method)
        c.drawString(col_au, y, sample.au_display)
        y -= 0.2 * inch

    if content.notes:
        y -= 0.2 * inch
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(left, y, f"Notes: {content.notes}")

    c.showPage()
    c.save()
    return buf.getvalue()

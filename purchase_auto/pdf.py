from __future__ import annotations

from pathlib import Path


def _pdf_text(value: str) -> str:
    safe = value.encode("latin-1", "replace").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_minimal_pdf(path: Path, title: str, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text_lines = [_pdf_text(title), "", *[_pdf_text(line) for line in lines]]
    body = ["BT", "/F1 12 Tf", "14 TL", "72 760 Td"]
    for index, line in enumerate(text_lines):
        if index:
            body.append("T*")
        body.append(f"({line}) Tj")
    body.append("ET")
    stream = "\n".join(body).encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    payload = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(obj)
        payload.extend(b"\nendobj\n")

    xref_at = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_at}\n"
            "%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(payload))
    return path

import io
import zipfile

from app.extract import extract_text


def make_pdf(pages: list[str]) -> bytes:
    """PDF mínimo con una línea de texto por página."""
    count = len(pages)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{4 + 2 * i} 0 R".encode() for i in range(count))
        + f"] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R "
            "/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return out.getvalue()


def make_zip(parts: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        for name, content in parts.items():
            package.writestr(name, content)
    return out.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def test_reads_pdf_pages():
    text = extract_text(make_pdf(["Integrales por partes", "Series de potencias"]), "pdf")

    assert text == "[Página 1]\nIntegrales por partes\n\n[Página 2]\nSeries de potencias"


def test_reads_word_paragraphs():
    xml = (
        f"<w:document {W}><w:body>"
        "<w:p><w:r><w:t>Unidad 1: </w:t></w:r><w:r><w:t>Límites</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Definición épsilon-delta</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )

    assert extract_text(make_zip({"word/document.xml": xml}), "docx") == (
        "Unidad 1: Límites\nDefinición épsilon-delta"
    )


def test_reads_powerpoint_slides_in_order():
    def slide(text):
        return f"<p:sld {A} xmlns:p='p'><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>"

    # slide10 va después de slide2, aunque alfabéticamente iría antes
    parts = {f"ppt/slides/slide{n}.xml": slide(f"Tema {n}") for n in (1, 2, 10)}
    text = extract_text(make_zip(parts), "pptx")

    assert text.index("Tema 2") < text.index("Tema 10")
    assert text.startswith("[Diapositiva 1]\nTema 1")


def test_reads_opendocument_text():
    xml = (
        '<office:document-content xmlns:office="o" xmlns:text="t">'
        "<text:h>Capítulo 2</text:h><text:p>Derivadas</text:p></office:document-content>"
    )

    assert extract_text(make_zip({"content.xml": xml}), "odt") == "Capítulo 2\nDerivadas"


def test_damaged_files_have_empty_text():
    assert extract_text(b"no es un pdf", "pdf") == ""
    assert extract_text(b"no es un zip", "docx") == ""


def test_images_and_spreadsheets_have_no_text():
    assert extract_text(b"\x89PNG", "png") is None
    assert extract_text(b"PK", "xlsx") is None

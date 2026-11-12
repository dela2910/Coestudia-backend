"""Texto de los archivos del grupo, para que lo lea el asistente de estudio (B13).

Se extrae al subir el archivo y se guarda en Archivo.texto_extraido. Los formatos que no se pueden
leer (imágenes, planillas Excel, Word antiguo .doc) quedan sin texto y el asistente no los usa.
"""

import codecs
import io
import logging
import re
import zipfile
from xml.etree import ElementTree

logger = logging.getLogger("coestudia.extract")

TEXT_EXTENSIONS = {"txt", "md", "csv"}
# Formatos de Office y OpenDocument: un ZIP con XML adentro
ZIP_EXTENSIONS = {"docx", "pptx", "odt", "odp"}
EXTRACTABLE_EXTENSIONS = TEXT_EXTENSIONS | ZIP_EXTENSIONS | {"pdf"}

# Tope de lo que se descomprime de un ZIP, contra archivos que se inflan al abrirlos
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024


def decode_text(data: bytes) -> str:
    """Texto de un archivo .txt, .md o .csv. Además de UTF-8 reconoce UTF-16, que es lo que guarda
    Excel como «texto Unicode»."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16", errors="replace")
    elif b"\x00" in data[:2]:
        # UTF-16 sin BOM: el byte en cero va después de cada letra común (LE) o antes (BE)
        encoding = "utf-16-le" if data[1:2] == b"\x00" else "utf-16-be"
        text = data.decode(encoding, errors="replace")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    return text


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader  # Solo se importa si se sube un PDF

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[Página {number}]\n{text}")
    return "\n\n".join(pages)


def _xml_paragraphs(xml: bytes) -> list[str]:
    """Párrafos de un XML de Word, PowerPoint u OpenDocument, en orden."""
    paragraphs = []
    for element in ElementTree.fromstring(xml).iter():
        tag = element.tag.rsplit("}", 1)[-1]
        # Word y PowerPoint usan <p>; OpenDocument usa <p> y <h> (títulos)
        if tag in ("p", "h"):
            text = "".join(element.itertext()).strip()
            if text:
                paragraphs.append(text)
    return paragraphs


def _zip_text(data: bytes, ext: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        names = package.namelist()
        if ext == "docx":
            parts = ["word/document.xml"]
        elif ext == "pptx":
            # slide1.xml, slide2.xml, …, slide10.xml: se ordenan por número, no alfabéticamente
            slides = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
            parts = sorted(slides, key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1])[0]))
        else:
            parts = ["content.xml"]
        if sum(package.getinfo(p).file_size for p in parts if p in names) > MAX_UNZIPPED_BYTES:
            raise ValueError("El archivo descomprimido es demasiado grande")

        sections = []
        for number, part in enumerate(parts, start=1):
            if part not in names:
                continue
            text = "\n".join(_xml_paragraphs(package.read(part)))
            if text and ext == "pptx":
                text = f"[Diapositiva {number}]\n{text}"
            if text:
                sections.append(text)
        return "\n\n".join(sections)


def extract_text(data: bytes, ext: str) -> str | None:
    """Texto del archivo, o None si el formato no se puede leer. Un PDF escaneado (solo imágenes)
    o un archivo dañado devuelven un texto vacío."""
    if ext not in EXTRACTABLE_EXTENSIONS:
        return None
    try:
        if ext in TEXT_EXTENSIONS:
            text = decode_text(data)
        elif ext == "pdf":
            text = _pdf_text(data)
        else:
            text = _zip_text(data, ext)
    except Exception:
        # Un archivo dañado no debe impedir subirlo: solo queda sin texto para el asistente
        logger.warning("No se pudo extraer el texto de un archivo .%s", ext, exc_info=True)
        text = ""
    # PostgreSQL no acepta el carácter NUL en un texto
    return text.replace("\x00", "").strip()

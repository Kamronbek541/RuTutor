# doc_parser.py — Extract text from DOCX and PDF documents
from __future__ import annotations

import os
import tempfile
from typing import Optional


def extract_text_from_docx(file_path: str) -> str:
    """Extract all text from a DOCX file, preserving paragraph breaks."""
    import docx
    doc = docx.Document(file_path)
    paragraphs = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def extract_text_from_pdf(file_path: str) -> str:
    """Extract all text from a PDF file using PyMuPDF."""
    import fitz  # pymupdf
    doc = fitz.open(file_path)
    pages = []
    for page in doc:
        text = page.get_text().strip()
        if text:
            pages.append(text)
    doc.close()
    return "\n\n".join(pages)


def extract_text(file_path: str) -> str:
    """Auto-detect format and extract text. Supports .docx and .pdf."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext == ".pdf":
        return extract_text_from_pdf(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}. Use .docx or .pdf")


def save_telegram_file(bot, file_info, original_name: str) -> str:
    """Downloads a Telegram file and saves it to a temp path with the correct extension."""
    ext = os.path.splitext(original_name)[1].lower() or ".docx"
    downloaded = bot.download_file(file_info.file_path)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(downloaded)
    tmp.close()
    return tmp.name

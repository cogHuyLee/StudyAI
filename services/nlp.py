"""Tiền xử lý văn bản tiếng Việt: tách từ, chia đoạn, đọc file tài liệu."""
import io
import re

try:  # Tuỳ chọn: tách từ tiếng Việt chuẩn hơn
    from underthesea import word_tokenize as _vi_tokenize
except Exception:  # pragma: no cover
    _vi_tokenize = None

STOPWORDS = set("""
và của là có trong cho được với các những một này đó để khi thì mà như về từ
ra vào đã sẽ đang cũng rất nên hay hoặc nhưng nếu vì bởi tại theo trên dưới
không còn lại bị do ở đến người nào gì sao thế ai bao nhiêu hãy xin
""".split())

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str):
    """Chuẩn hoá -> tách từ -> bỏ stopword."""
    text = (text or "").lower()
    if _vi_tokenize:
        tokens = _vi_tokenize(text, format="text").split()
    else:
        tokens = _WORD_RE.findall(text)
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def split_sentences(text: str):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def chunk_text(text: str, size: int = 600):
    """Chia văn bản thành các đoạn ~size ký tự, gối đầu 1 câu để giữ ngữ cảnh."""
    chunks, current = [], []
    length = 0
    for sent in split_sentences(text):
        if length + len(sent) > size and current:
            chunks.append(" ".join(current))
            current = [current[-1]]            # câu gối đầu
            length = len(current[0])
        current.append(sent)
        length += len(sent)
    if current:
        chunks.append(" ".join(current))
    return [c for c in chunks if len(c) > 20]


def extract_text(file_storage) -> str:
    """Đọc nội dung từ .txt / .md / .pdf / .docx."""
    name = (file_storage.filename or "").lower()
    data = file_storage.read()
    if name.endswith((".txt", ".md")):
        for enc in ("utf-8", "utf-16", "cp1258", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    if name.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs)
    raise ValueError("Chỉ hỗ trợ file .txt, .md, .pdf, .docx")

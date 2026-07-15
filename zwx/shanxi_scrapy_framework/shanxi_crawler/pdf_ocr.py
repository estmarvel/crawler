import base64
import hashlib
import os
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from shanxi_crawler.ai_service import get_ai_client, sleep_before_ai_call, ai_retry_sleep, TOKEN_COUNTER
from shanxi_crawler.text_utils import normalize_text


def extract_pdf_url_from_html(html: str, page_url: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    candidates = []
    for tag, attr in [("iframe", "src"), ("embed", "src"), ("object", "data"), ("a", "href")]:
        for el in soup.select(tag):
            u = el.get(attr, "")
            if u and ".pdf" in u.lower():
                candidates.append(u.strip())
    if not candidates:
        return ""
    pdf_url = candidates[0]
    if pdf_url.startswith("//"):
        pdf_url = "https:" + pdf_url
    elif pdf_url.startswith("/") or not pdf_url.startswith("http"):
        pdf_url = urljoin(page_url, pdf_url)
    return pdf_url


def _safe_name(key: str, suffix: str) -> str:
    return hashlib.md5(key.encode("utf-8")).hexdigest() + suffix


def save_pdf_bytes(pdf_url: str, body: bytes) -> str:
    cache_dir = Path(os.getenv("PDF_CACHE_DIR", "pdf_cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / _safe_name(pdf_url, ".pdf")
    if not path.exists() or path.stat().st_size <= 1024:
        path.write_bytes(body)
    return str(path)


def extract_text_from_pdf(pdf_path: str, max_pages: int = None) -> str:
    if max_pages is None:
        max_pages = int(os.getenv("PDF_TEXT_MAX_PAGES", "20"))
    try:
        import fitz
    except Exception:
        return ""
    try:
        doc = fitz.open(pdf_path)
        texts = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text = page.get_text("text") or ""
            if text.strip():
                texts.append(text)
        doc.close()
        return normalize_text("\n".join(texts))
    except Exception:
        return ""


def render_pdf_pages_to_images(pdf_path: str) -> list:
    try:
        import fitz
    except Exception:
        return []
    max_pages = int(os.getenv("OCR_MAX_PAGES", "10"))
    dpi = int(os.getenv("OCR_DPI", "160"))
    image_dir = Path(os.getenv("OCR_CACHE_DIR", "ocr_cache")) / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_paths = []
    try:
        doc = fitz.open(pdf_path)
        key = hashlib.md5((pdf_path + str(os.path.getmtime(pdf_path))).encode("utf-8")).hexdigest()
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            img_path = image_dir / f"{key}_{i+1}.png"
            if not img_path.exists() or img_path.stat().st_size <= 1024:
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                pix.save(str(img_path))
            image_paths.append(str(img_path))
        doc.close()
    except Exception:
        return []
    return image_paths


def image_to_data_url(image_path: str) -> str:
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def ocr_image_with_qwen(image_path: str, page_no: int = 1, retries: int = 2) -> str:
    client = get_ai_client()
    data_url = image_to_data_url(image_path)
    prompt = (
        "请对这张招标公告PDF页面做OCR识别，只输出页面中的原始文字。"
        "保持段落顺序，不要总结，不要解释，不要补充不存在的内容。"
        "如果有表格，也请按从上到下、从左到右的阅读顺序输出。"
    )
    for attempt in range(retries + 1):
        try:
            sleep_before_ai_call()
            resp = client.chat.completions.create(
                model=os.getenv("OCR_MODEL", "qwen-vl-ocr"),
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }],
                temperature=0.0,
                max_tokens=6000,
            )
            TOKEN_COUNTER.add(resp.usage)
            return normalize_text(resp.choices[0].message.content.strip())
        except Exception:
            if attempt < retries:
                ai_retry_sleep(attempt)
    return ""


def ocr_pdf_with_qwen(pdf_url: str, pdf_path: str) -> str:
    cache_dir = Path(os.getenv("OCR_CACHE_DIR", "ocr_cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    txt_path = cache_dir / _safe_name(pdf_url, ".txt")
    if txt_path.exists() and txt_path.stat().st_size > 100:
        return normalize_text(txt_path.read_text(encoding="utf-8"))
    image_paths = render_pdf_pages_to_images(pdf_path)
    if not image_paths:
        return ""
    texts = []
    for i, img in enumerate(image_paths, start=1):
        text = ocr_image_with_qwen(img, i)
        if text:
            texts.append(f"【第{i}页】\n{text}")
    full_text = normalize_text("\n".join(texts))
    if full_text:
        txt_path.write_text(full_text, encoding="utf-8")
    return full_text


def get_pdf_text_from_response(pdf_url: str, body: bytes) -> str:
    pdf_path = save_pdf_bytes(pdf_url, body)
    text = extract_text_from_pdf(pdf_path)
    min_chars = int(os.getenv("PDF_TEXT_MIN_CHARS", "500"))
    if len(text) >= min_chars:
        return text
    if os.getenv("ENABLE_PDF_OCR", "true").lower() != "true":
        return text
    if not os.getenv("DMX_API_KEY", "").strip():
        return text
    ocr_text = ocr_pdf_with_qwen(pdf_url, pdf_path)
    return ocr_text or text

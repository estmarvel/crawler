"""
=============================================================================
华新阳光采购平台 - PDF 公告处理器
=============================================================================
处理逻辑:
  1. 从详情 API 获取 annContent 和 pdfFile 字段
  2. 若 annContent 为空且 pdfFile 有值, 则是 PDF 类型公告
  3. 尝试通过文件 API 下载 PDF → pdfplumber 提取文本
  4. 若下载失败, 标注 "PDF文档（需手动下载）"

PDF 下载方式说明:
  平台 PDF 文件存储在内部文件系统中, 通过 `pdfFile` ID 访问.
  前端通过浏览器内嵌阅读器渲染, 后端可能有以下端点:
    - GET /web/file/query/{pdfId}     → 返回文件流 (需带 authentication header)
    - GET /file/download/{pdfId}      → 可能的备用路径
    - GET /file/preview/{pdfId}       → 可能的备用路径

  若都失败, 则在公告内容字段写入说明信息,
  并将 pdfFile ID 写入备注字段供手动下载.
=============================================================================
"""

import io
import logging
import time
from typing import Optional

import requests

try:
    from . import settings as config
except ImportError:
    import settings as config  # type: ignore

logger = logging.getLogger(__name__)


class PDFHandler:
    """PDF 公告下载与文本提取"""

    # 已知的可能 PDF 下载端点
    _PDF_ENDPOINTS = [
        "/web/file/query/{pdf_id}",
        "/file/download/{pdf_id}",
        "/file/preview/{pdf_id}",
        "/web/file/download/{pdf_id}",
    ]

    # PDF 文件魔数
    _PDF_MAGIC = b"%PDF"

    def __init__(self, token: str, proxy: Optional[str] = None):
        self._token = token
        self._proxy = proxy
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.verify = False
            self._session.headers.update({
                "authentication": self._token,
                "Origin": config.BASE_URL,
                "Referer": f"{config.BASE_URL}/",
                "User-Agent": config.USER_AGENTS[0],
            })
        return self._session

    def is_pdf_announcement(self, detail: dict) -> bool:
        """判断公告是否为 PDF 类型"""
        ann_content = detail.get("annContent") or ""
        pdf_file = detail.get("pdfFile") or ""

        # 内容为空 且 存在 PDF 文件ID → PDF 类型
        if not ann_content.strip() and pdf_file:
            return True
        # 内容很短且是 iframe 嵌入 PDF 的标签
        if ann_content and len(ann_content.strip()) < 200:
            if "pdf" in ann_content.lower() or "iframe" in ann_content.lower():
                return True
        return False

    def download_text(self, detail: dict) -> str:
        """
        下载 PDF 并提取文本.
        失败返回标记文本供后续处理.
        """
        pdf_id = detail.get("pdfFile") or ""
        ann_id = detail.get("annId", "unknown")

        if not pdf_id:
            return ""

        session = self._get_session()

        for i, endpoint_tpl in enumerate(self._PDF_ENDPOINTS):
            url = f"{config.API_BASE}{endpoint_tpl.format(pdf_id=pdf_id)}"
            try:
                logger.debug(f"[PDF] 尝试下载: {url}")
                resp = session.get(
                    url,
                    timeout=config.REQUEST_TIMEOUT,
                    proxies={"http": self._proxy, "https": self._proxy} if self._proxy else None,
                )
                if resp.status_code == 200 and self._is_pdf_bytes(resp.content):
                    logger.info(f"[PDF] 下载成功 annId={ann_id}, {len(resp.content)} bytes")
                    return self._extract_text(resp.content, ann_id)

                # 如果是 JSON 返回 (可能含重定向 URL)
                if "application/json" in resp.headers.get("Content-Type", ""):
                    try:
                        data = resp.json()
                        url_in_json = data.get("data") or data.get("url") or ""
                        if url_in_json and isinstance(url_in_json, str) and url_in_json.startswith("http"):
                            return self._download_from_url(url_in_json, ann_id)
                    except Exception:
                        pass

                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"[PDF] 端点 {endpoint_tpl} 请求失败: {e}")
                continue

        # 全部端点都失败
        logger.warning(f"[PDF] 无法下载 annId={ann_id} pdfId={pdf_id}")
        return (
            "【PDF公告 - 内容需在线查看】\n"
            f"公告ID: {ann_id}\n"
            f"PDF文件ID: {pdf_id}\n"
            f"在线地址: {config.BASE_URL}/#/biddingDetail?annId={ann_id}\n"
        )

    def _download_from_url(self, pdf_url: str, ann_id: str) -> str:
        """从 JSON 返回的 URL 下载 PDF"""
        try:
            resp = self._get_session().get(
                pdf_url,
                timeout=config.REQUEST_TIMEOUT,
                proxies={"http": self._proxy, "https": self._proxy} if self._proxy else None,
            )
            if resp.status_code == 200:
                return self._extract_text(resp.content, ann_id)
        except Exception as e:
            logger.warning(f"[PDF] URL 下载失败: {e}")
        return ""

    def _is_pdf_bytes(self, content: bytes) -> bool:
        """检查字节流是否是 PDF 格式"""
        return content[:4] == self._PDF_MAGIC

    def _extract_text(self, pdf_bytes: bytes, ann_id: str) -> str:
        """从 PDF 字节提取文本"""
        texts = []

        # 方式1: pdfplumber (精确度高)
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        texts.append(text)
            if texts:
                logger.info(f"[PDF] pdfplumber 提取成功 annId={ann_id}, {len(pdf.pages)}页")
                return "\n".join(texts)
        except ImportError:
            logger.debug("[PDF] pdfplumber 未安装, 回退 PyPDF2")
        except Exception as e:
            logger.debug(f"[PDF] pdfplumber 提取失败: {e}")

        # 方式2: PyPDF2 (保底)
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    texts.append(text.strip())
            if texts:
                logger.info(f"[PDF] PyPDF2 提取成功 annId={ann_id}, {len(reader.pages)}页")
                return "\n".join(texts)
        except ImportError:
            logger.debug("[PDF] PyPDF2 未安装")
        except Exception as e:
            logger.warning(f"[PDF] PyPDF2 提取失败: {e}")

        # 方式3: 标记不可提取
        return (
            "【PDF公告 - 文本提取失败，需在线查看】\n"
            f"公告ID: {ann_id}\n"
            f"文件大小: {len(pdf_bytes)} bytes\n"
            f"在线地址: {config.BASE_URL}/#/biddingDetail?annId={ann_id}\n"
        )

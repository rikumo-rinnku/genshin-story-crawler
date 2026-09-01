"""
公共文本清洗工具
"""
import re
from bs4 import BeautifulSoup


BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "aside",
    "blockquote", "pre", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
}


def clean_html_to_text(html: str) -> str:
    """提取 HTML 正文，仅保留真实的段落和换行。

    不使用 BeautifulSoup 的全标签分隔符：它会把 span、a 等内联标签也
    拆成换行，进而损坏词条中的字段值。br 和块级标签才代表可保留的断行。
    """
    soup = BeautifulSoup(html, 'lxml')
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_after("\n")

    text = soup.get_text()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[\t \f\v]*\n[\t \f\v]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_html_to_inline_text(html: str) -> str:
    """提取字段型 HTML，并将其压缩为单行文本。"""
    return " ".join(clean_html_to_text(html).split())

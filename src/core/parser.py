"""
公共文本清洗工具
"""
import re
from bs4 import BeautifulSoup

def clean_html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text(separator='\n', strip=True)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()
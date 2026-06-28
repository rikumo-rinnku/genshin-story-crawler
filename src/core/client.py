"""
统一 HTTP 请求模块（支持重试、头、代理）
"""
import requests
from tenacity import retry, stop_after_attempt, wait_random_exponential

@retry(stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1, max=10))
def get(url, headers=None, params=None, timeout=30):
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp

def post(url, data=None, headers=None, json=None):
    # 暂未使用，但保留接口
    pass
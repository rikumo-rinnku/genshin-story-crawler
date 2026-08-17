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
    # 某些上游接口会偶发返回 HTTP 200 但正文被截断或不是完整 JSON。
    # 在请求层提前验证，可让 tenacity 将其视为可重试故障，避免模块
    # 在解析目录时直接中断。
    resp.json()
    return resp

@retry(stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1, max=10))
def post(url, data=None, headers=None, json=None, timeout=30):
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.post(url, data=data, headers=headers, json=json, timeout=timeout)
    resp.raise_for_status()
    resp.json()
    return resp

import requests

def main():
    url = "https://baike.mihoyo.com/ys/obc/channel/map/189/25"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers)
    print(f"状态码: {resp.status_code}")
    print(f"页面长度: {len(resp.text)} 字符")

if __name__ == "__main__":
    main()
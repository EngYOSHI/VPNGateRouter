#!/usr/bin/env python3.11

import os
import requests
import re
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from pathlib import Path
import time
from threading import Thread

CHECK_URL = "http://cloudflare.com/cdn-cgi/trace"
INTERVAL = 6


def main():
    wc = Thread(target=web_con_worker, daemon=True)
    wc.start()
    while wc.is_alive():
        time.sleep(1)


def web_con_worker():
    while True:
        web_con(CHECK_URL)
        time.sleep(INTERVAL)


def web_con(url: str) -> bool:
    try:
        start = time.perf_counter()
        res = requests.get(url, timeout=3)
        end = time.perf_counter()
    except Exception as ex:
        # エラー
        log_write("web", 1, ex)
    else:
        if res.status_code == 200:
            match = re.search(r"^ip=([^\s]+)", res.text, re.MULTILINE)
            if match:
                ms = (end - start) * 1000
                ip = match.group(1)
                log_write("web", 0, f"{ip}; {ms:.3f}")
            else:
                # データのパースに失敗．ほぼ起こり得ないはず．
                log_write("web", 2, "Parse error")
        else:
            # ステータスコードが200でない．サーバサイドの問題で起こり得る
            log_write("web", 3, res.status_code)


def log_write(type: str, code: int, msg: str):
    dt = datetime.now(ZoneInfo("Asia/Tokyo"))
    path = Path(__file__).resolve().parent.joinpath(f"check_log/{type}-{dt.date()}.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode="a", encoding="utf-8", newline='\n') as f:
        f.write(f"{dt}; {code}; {msg}\n")
    print(f"{dt}; {code}; {msg}")


if __name__ == "__main__":
    os.system("")  # Windowsにて、色付き文字を出力するためのおまじない
    main()
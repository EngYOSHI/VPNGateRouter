#!/usr/bin/env python3.11

import os
import requests
import re
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from pathlib import Path
import time
from threading import Thread
import dns.resolver

CHECK_URL = "http://104.16.132.229/cdn-cgi/trace"
DNS_DOMAIN = "www.google.com"
DNS_NAMESERVERS = ["1.1.1.1"]
INTERVAL = 5


def main():
    wc = Thread(target=worker, daemon=True)
    wc.start()
    while wc.is_alive():
        time.sleep(1)


def worker():
    while True:
        t = Thread(target=web_con, args=(CHECK_URL,), daemon=True)
        t.start()
        t2 = Thread(target=dns_check, args=(DNS_DOMAIN, DNS_NAMESERVERS), daemon=True)
        t2.start()
        time.sleep(INTERVAL)


def web_con(url: str):
    dt = datetime.now(ZoneInfo("Asia/Tokyo"))
    type = "web"
    try:
        start = time.perf_counter()
        res = requests.get(url, timeout=INTERVAL)
        end = time.perf_counter()
    except Exception as ex:
        # エラー
        log_write(dt, type, 1, ex)
    else:
        if res.status_code == 200:
            match = re.search(r"^ip=([^\s]+)", res.text, re.MULTILINE)
            if match:
                ms = (end - start) * 1000
                ip = match.group(1)
                log_write(dt, type, 0, f"{ip}; {ms:.3f}")
            else:
                # データのパースに失敗．ほぼ起こり得ないはず．
                log_write(dt, type, 2, "Parse error")
        else:
            # ステータスコードが200でない．サーバサイドの問題で起こり得る
            log_write(dt, type, 3, res.status_code)


def dns_check(fqdn: str, ns: list[str]):
    dt = datetime.now(ZoneInfo("Asia/Tokyo"))
    type = "dns"
    try:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = ns
        resolver.timeout = 5
        start = time.perf_counter()
        answers = resolver.resolve(fqdn, 'a')
        end = time.perf_counter()
    except Exception as ex:
        # エラー
        log_write(dt, type, 1, ex)
    else:
        ms = (end - start) * 1000
        a = ", ".join([rdata.address for rdata in answers])
        log_write(dt, type, 0, f"{a}; {ms:.3f}")


def log_write(dt, type: str, code: int, msg: str):
    path = Path(__file__).resolve().parent.joinpath(f"check_log/{type}-{dt.date()}.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode="a", encoding="utf-8", newline='\n') as f:
        f.write(f"{dt}; {code}; {msg}\n")
    print(f"[{type}] {dt}; {code}; {msg}")


if __name__ == "__main__":
    os.system("")  # Windowsにて、色付き文字を出力するためのおまじない
    main()
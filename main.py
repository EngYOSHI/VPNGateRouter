#!/usr/bin/env python3.11

import os
import csv
import requests
import base64
import re
from io import StringIO
import time

CSV_URL = "https://www.vpngate.net/api/iphone/"
DEBUG = True


def main():
    print_debug("Started.")
    server_list = get_server_list()  # VPNGateのサーバ情報を取得


def get_server_list(country: str = None, port: int = None):
    res = []
    with requests.Session() as s:
        print_debug("Getting VPNGate server list csv.")
        while True:
            try:
                content = s.get(CSV_URL).content.decode("utf-8")
                break  # contentをループ外で使うため
            except Exception as e:
                print_error("GetServerListCSV", e)
                time.sleep(3)
                continue
        server_list = list(csv.reader(StringIO(content), delimiter=","))
        # [0]HostName,[1]IP,[2]Score,[3]Ping,[4]Speed,
        # [5]CountryLong,[6]CountryShort,[7]NumVpnSessions,[8]Uptime,
        # [9]TotalUsers,[10]TotalTraffic,[11]LogType,[12]Operator,
        # [13]Message,[14]OpenVPN_ConfigData_Base64
        server_list = server_list[2:-1]  # 1,2行目と最終行は不要な情報
        print_debug("▼ServerList")
        for s in server_list:
            # hostname, ip, port, score, ping, speed,
            # country, num_vpn_sessions, uptime, operator
            sinfo = ServerConnectInfo(
                s[0],
                s[1],
                get_port_from_openvpn(s[14]),
                s[2],
                s[3],
                s[4],
                s[6],
                s[7],
                s[8],
                s[12],
            )
            if country is not None and sinfo.country != country:
                continue
            if port is not None and sinfo.port != port:
                continue
            res.append(sinfo)
            print_debug(repr(sinfo), False)
        res.sort(key=lambda x: x.score)
        return res


def get_port_from_openvpn(base64str):
    """
    openvpnのbase64から、TCPポート番号を抽出する
    ただし、"proto tcp"がない場合や、ポート番号が抽出できなかった場合はNoneを返す

    Args:
        base64str (string): base64文字列

    Returns:
        int: TCPポート番号
    """
    s = base64.b64decode(base64str).decode()
    if "proto tcp" in s:
        match = re.search(r"remote \d{1,3}(?:\.\d{1,3}){3} (\d+)", s)
        if match:
            port = int(match.group(1))
            return port
    print_error("OpenVPNConfigErr", "TCP not supported or format error.  Ignored.")
    return None


class ServerConnectInfo:
    def __init__(
        self,
        hostname,
        ip,
        port,
        score,
        ping,
        speed,
        country,
        num_vpn_sessions,
        uptime,
        operator,
    ):
        self.hostname = hostname
        self.ip = ip
        self.port = port
        self.score = score
        self.ping = ping
        self.speed = speed
        self.country = country
        self.num_vpn_sessions = num_vpn_sessions
        self.uptime = uptime
        self.operator = operator

    def get_speed(self):
        unit = ["B", "KB", "MB", "GB", "TB", "PB"]
        index_unit = 0
        speed = int(self.speed)
        while True:
            if speed >= 1000:
                index_unit += 1
                speed /= 1000
            else:
                break
        return f"{speed:.2f}{unit[index_unit]}/s"

    def __repr__(self):
        return f"{self.hostname}: {self.ip}:{self.port} ({self.country}) Score:{self.score} Ping:{self.ping}ms {self.get_speed()}"


def print_debug(msg, banner=True, end="\n"):
    if DEBUG:
        if banner:
            print("\033[45m(DEBUG)\033[0m " + str(msg), end=end)
        else:
            print(str(msg), end=end)


def print_error(errtype, errmsg):
    print("\033[31m" + str(errtype) + ": " + str(errmsg) + "\033[0m")


if __name__ == "__main__":
    os.system("")  # Windowsにて、色付き文字を出力するためのおまじない
    main()

#!/usr/bin/env python3.11

import os
import csv
import requests
import base64
import re
from io import StringIO
import time
import subprocess
from threading import Thread, Event

CSV_URL: str = "https://www.vpngate.net/api/iphone/"
DEBUG: bool = False
IP_LOCAL: str = "192.168.19.0/24"
IP_GATEWAY: str = "10.0.10.254"
NIC_UPSTREAM: str = "eth0"
NIC_VPN: str = "br_eth1"
NIC_VPNGATE: str = "vpn_vpngate"
VPNGATE_FIX: str = None  # "118.106.1.118:1496"
VPNGATE_COUNTRY: str = "JP"
VPNGATE_PORT: int = 443

status_error_event = Event()


def main():
    last: str = None  # 最後に接続したサーバ
    try:
        print_debug("Started.")
        init()  # 初期設定
        while True:
            # ベストなVPNGateのサーバ情報を取得
            host = get_bestserver(last, VPNGATE_COUNTRY, VPNGATE_PORT)
            vpngateip = host.split(":")[0]  # IPアドレス部分を抽出
            last = vpngateip
            vpn_connect(host)  # ベストなVPNGateサーバに接続
            ipconfig(vpngateip)  # IPアドレスを設定
            # 死活監視スレッドを実行
            sc = Thread(target=status_check_worker, daemon=True)
            sc.start()
            while sc.is_alive():
                if status_error_event.wait(timeout=1.0):
                    status_error_event.clear()
                    # 状態エラー発生のためフェイルオーバー開始
                    print("Failover started.")
                    ipreset(vpngateip)  # IP設定を解除
                    vpn_disconnect()  # VPN切断
                    break
    except KeyboardInterrupt:
        print("exitting...")


def init():
    # IPマスカレードの設定
    print("Setting up ip masquerade...")
    res = runcmd(
        [
            "iptables",
            "-t",
            "nat",
            "-A",
            "POSTROUTING",
            "-s",
            IP_LOCAL,
            "-o",
            NIC_VPNGATE,
            "-j",
            "MASQUERADE",
        ]
    )
    if res.returncode != 0:
        print_error(
            "NAT Config",
            f"iptables command failed. Error information is below.\n{res.stderr}",
        )


def status_check_worker():
    print("Status check process is running.")
    while True:
        (valid, status) = vpn_status("Session Status")
        if valid and status == "Connection Completed (Session Established)":
            time.sleep(1)
            continue
        else:
            print_error("StatusCheck", "Connection error detected.", False)
            status_error_event.set()
            time.sleep(1)  # イベント発火を確実にさせる起こすため念のため
            return


def ipconfig(vpngateip: str):
    # DHCPにてIP取得
    print("Obtaining IP Address from vpngate server...")
    open("lease.txt", "w").close()  # lease情報の保存先を作成
    res = runcmd(
        ["dhclient", "-v", "-sf", "/bin/true", "-lf", "lease.txt", "vpn_vpngate"]
    )
    if res.returncode != 0:
        print_error(
            "dhclient", f"dhclient failed. Error information is below.\n{res.stderr}"
        )
    # 情報抽出
    with open("lease.txt", "r") as f:
        lease_text = f.read()
    print_debug(f"DHCP Lease information\n{lease_text}")
    fixed_address_match = re.search(r"fixed-address\s+([\d.]+);", lease_text)
    fixed_address = fixed_address_match.group(1) if fixed_address_match else None
    routers_match = re.search(r"option routers\s+([\d.]+);", lease_text)
    routers = routers_match.group(1) if routers_match else None
    if fixed_address is None or routers is None:
        print_error("ParseDHCPData", "Obtained dhcp data was not valid.")
    fixed_address += "/16"
    print(f"Obtained IP: {fixed_address}  GW:{routers}")
    # 静的経路設定
    res = runcmd(
        ["ip", "route", "add", vpngateip, "via", IP_GATEWAY, "dev", NIC_UPSTREAM]
    )
    if res.returncode != 0:
        print_error(
            "IP Route Add",
            f"ip route add failed. Error information is below.\n{res.stderr}",
        )
    # IP設定
    res = runcmd(["ip", "addr", "add", fixed_address, "dev", NIC_VPNGATE])
    if res.returncode != 0:
        print_error(
            "IP Addr Add",
            f"ip addr add failed. Error information is below.\n{res.stderr}",
        )
    res = runcmd(["ip", "route", "add", "default", "via", routers, "dev", NIC_VPNGATE])
    if res.returncode != 0:
        print_error(
            "IP Route Add Default",
            f"ip addr add default failed. Error information is below.\n{res.stderr}",
        )
    res = runcmd(["curl", "inet-ip.info"])
    if res.returncode != 0:
        print_error(
            "GetWANIP", f"curl failed. Error information is below.\n{res.stderr}"
        )
    print(f"IP Configuration OK. WAN IP: {res.stdout}")


def ipreset(vpngateip: str):
    print("Resetting IP setting...")
    # 静的経路設定解除
    res = runcmd(["ip", "route", "del", vpngateip])
    if res.returncode != 0:
        print_error(
            "IP Route Del",
            f"ip route del failed. Error information is below.\n{res.stderr}",
        )
    # IP解放
    res = runcmd(["ip", "addr", "flush", "dev", NIC_VPNGATE])
    if res.returncode != 0:
        print_error(
            "IP Addr Flush",
            f"ip addr flush failed. Error information is below.\n{res.stderr}",
        )


def get_bestserver(last, country, port) -> str:
    print("Getting best vpngate server...")
    server_list = get_server_list(country, port)
    if len(server_list) == 0:
        print_error("GetBestServer", "No server found.")
    if last is not None:
        # 最後に接続していたサーバは除外
        for server in server_list:
            if server.ip == last:
                server_list.remove(server)
    print(f"Done. Info:{server_list[0]}")
    return server_list[0].get_host()


def vpn_connect(host: str):
    # 接続情報の設定
    print("Setting vpngate server address...")
    res = runvpncmd(["accountset", "vpngate", f"/server:{host}", "/hub:vpngate"])
    if errcheck_vpncmd_res(res):
        print_error(
            "VPNCMD_Set",
            f"Accountset command failed. Error information is below.\n{res.stdout}",
        )
    # 接続
    print("Connecting to vpngate server...")
    res = runvpncmd(["accountconnect", "vpngate"])
    if errcheck_vpncmd_res(res):
        print_error(
            "VPNCMD_Connect",
            f"Connect command failed. Error information is below.\n{res.stdout}",
        )
    # 接続状況確認
    print("Checking connection...")
    while True:
        (valid, status) = vpn_status("Session Status")
        if valid and status == "Connection Completed (Session Established)":
            break
        time.sleep(1)


def vpn_disconnect():
    # 切断
    print("Disconnecting from vpngate server...")
    res = runvpncmd(["accountdisconnect", "vpngate"])
    if errcheck_vpncmd_res(res):
        print_error(
            "VPNCMD_Disconnect",
            f"Disconnect command failed. Error information is below.\n{res.stdout}",
        )
    # 接続状況確認
    print("Checking connection...")
    while True:
        (valid, status) = vpn_status("Session Status")
        if not valid:
            break
        time.sleep(1)


def runcmd(command: list[str]) -> subprocess.CompletedProcess:
    res = subprocess.run(command, check=False, capture_output=True, text=True)
    print_debug(f"RunCMD_args: {' '.join(res.args)}")
    print_debug(f"RunCMD_stdout: {res.stdout}")
    print_debug(f"RunCMD_stderr: {res.stderr}")
    return res


def runvpncmd(command: list[str]) -> subprocess.CompletedProcess:
    command = ["vpncmd", "localhost", "/client", "/cmd"] + command
    return runcmd(command)


def vpn_status(key: str) -> (bool, str):
    res = runvpncmd(["accountstatusget", "vpngate"])
    if errcheck_vpncmd_res(res):
        return (False, None)
    match = re.search(rf"{re.escape(key)}\s+\|(.+)", res.stdout)
    if match:
        return (True, match.group(1).strip())
    else:
        return (False, None)


def errcheck_vpncmd_res(res: subprocess.CompletedProcess) -> bool:
    if res.stdout[-3].rfind("The command completed successfully."):
        return False
    return True


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

    def get_host(self):
        return f"{self.ip}:{self.port}"

    def __repr__(self):
        return f"{self.hostname}: {self.ip}:{self.port} ({self.country}) Score:{self.score} Ping:{self.ping}ms {self.get_speed()}"


def print_debug(msg, banner=True, end="\n"):
    if DEBUG:
        if banner:
            print("\033[45m(DEBUG)\033[0m " + str(msg), end=end)
        else:
            print(str(msg), end=end)


def print_error(errtype, errmsg, exit_after_print: bool = True):
    print("\033[31m" + str(errtype) + ": " + str(errmsg) + "\033[0m")
    if exit_after_print:
        exit(1)


def chkroot():
    if os.geteuid() != 0 or os.getuid() != 0:
        print_error("chkroot", "Run As Root!!!")


if __name__ == "__main__":
    os.system("")  # Windowsにて、色付き文字を出力するためのおまじない
    chkroot()
    main()

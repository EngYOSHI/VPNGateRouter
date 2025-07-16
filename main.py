#!/usr/bin/env python3.11

import sys
import os
import csv
import requests
import base64
import re
from io import StringIO
import time
import subprocess
from threading import Thread, Event
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from pathlib import Path

CSV_URL: str = "https://www.vpngate.net/api/iphone/"
DEBUG: bool = False
IP_LOCAL: str = "192.168.19.0/24"
NIC_UPSTREAM: str = "eth0"
NIC_VPN: str = "br_eth1"
NIC_VPNGATE: str = "vpn_vpngate"
VPNGATE_FIX: str = None  # "118.106.1.118:1496"
VPNGATE_COUNTRY: str = "JP"
VPNGATE_PORT: int = None

status_error_event = Event()
is_connected = False
is_overwrite_active = False


def main():
    global is_connected
    vpngateip_list: list[str] = []  # 最後に接続したサーバ
    try:
        print_debug("Started.")
        init()  # 初期設定
        while True:
            # ベストなVPNGateのサーバ情報を取得
            host = get_bestserver(vpngateip_list, VPNGATE_COUNTRY, VPNGATE_PORT)
            vpngateip_list.append(host.split(":")[0])  # IPアドレス部分を抽出
            connect_res = vpn_connect(host)  # ベストなVPNGateサーバに接続
            if not connect_res:
                print_error("VPNConnect", "Could not complete connecting to vpngate server.")
                # 接続失敗時，クリーンして再実行
                vpn_disconnect()
                print_debug(f"Bad servers: {vpngateip_list}")
                continue
            ipconfig(vpngateip_list[-1])  # IPアドレスを設定
            # 死活監視スレッドを実行
            is_connected = True
            # 接続成功したので，リストを現在接続している中継サーバのみとする
            vpngateip_list = [vpngateip_list[-1]]
            sc = Thread(target=status_check_worker, daemon=True)
            sc.start()
            dh = Thread(target=dhcp_reobtain_worker, daemon=True)
            dh.start()
            while sc.is_alive():
                if status_error_event.wait(timeout=1.0):
                    status_error_event.clear()
                    # 状態エラー発生のためフェイルオーバー開始
                    print_log("Failover started.")
                    ipreset(vpngateip_list[-1])  # IP設定を解除
                    vpn_disconnect()  # VPN切断
                    break
    except FatalErrException:
        clean(vpngateip_list[-1])
        err_exit()
    except KeyboardInterrupt:
        print_log("exiting...")
        clean(vpngateip_list[-1])
        print_log("Ready to exit. BYE!")


def init():
    # IPマスカレードの設定
    print_log("Setting up ip masquerade...")
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
        # IPアドレスの指定形式がおかしいなどの構文エラーの場合2
        # 存在しないNIC指定では正常終了
        # 通常発生し得ない
        print_error(
            "NAT Config",
            f"iptables command failed. Error information is below.\n{res.stderr}",
        )
        raise FatalErrException()


def clean(vpngateip):
    global is_connected
    is_connected = False
    ipreset(vpngateip)  # IP設定を解除
    vpn_disconnect()  # VPN切断
    # IPマスカレードの解除
    print_log("Cleaning ip masquerade setting...")
    res = runcmd(
        [
            "iptables",
            "-t",
            "nat",
            "-D",
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
            "NAT Reset",
            f"iptables command failed. Error information is below.\n{res.stderr}",
        )


def get_gw(nic: str):
    res = runcmd(
        ["ip", "route", "show", "default", "dev", str(nic)]
    )
    match = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", res.stdout)
    if match:
        gateway_ip = match.group(1)
        print_log(f"Gateway address of {nic} is {gateway_ip}")
        return gateway_ip
    else:
        # 結果が空：NICが存在しない，あるいはデフォルトルートがない場合が該当する
        # 結果がエラー：構文エラー(NIC指定が空白になっているなど)
        # 発生したらプログラムを続行すべきでない
        print_error(
            "GetGwAddr",
            f"NIC:{nic} is not found, or have no ip address."
        )
        raise FatalErrException()


def status_check_worker():
    global is_connected
    print_log("Status check process is running.")
    while is_connected:
        (valid, status, s) = vpn_status("Session Status", log_disp_out=False)
        if valid and status == "Connection Completed (Session Established)":
            show_status(s)
            time.sleep(1)
            continue
        else:
            if is_connected:
                print_error(
                    "StatusCheck", "Connection error detected."
                )
                is_connected = False
                status_error_event.set()
                time.sleep(1)  # イベント発火を確実にさせる起こすため念のため
            return


def show_status(s: str):
    match1 = re.search(r"Outgoing Data Size\s+\|([\d,]+) bytes", s)
    match2 = re.search(r"Incoming Data Size\s+\|([\d,]+) bytes", s)
    if match1 and match2:
        unit = ["bytes", "KB", "MB", "GB", "TB"]
        dout = conv_datasize(int(match1.group(1).replace(',', '')), unit)
        din = conv_datasize(int(match2.group(1).replace(',', '')), unit)
        print_status(f"DL:{din}  UP:{dout}")
    else:
        print_error("StatusCheck", "Failed to parse data size.")


def conv_datasize(i: int, unit: list[str]) -> str:
    index_unit = 0
    while index_unit < len(unit) - 1:
        if i >= 1000:
            index_unit += 1
            i /= 1000
        else:
            break
    return f"{i:.2f}{unit[index_unit]}"


def dhcp_reobtain_worker():
    counter: int = 0
    while is_connected:
        time.sleep(1)
        counter += 1
        if counter > 300:
            counter = 0
            print_debug("Reobtaining IP Address...")
            dhcp(loop=False, log_disp_out=False)


def dhcp(loop: bool = True, log_disp_out: bool = True) -> (str, str):
    while True:
        path = Path(__file__).resolve().parent.joinpath("lease.txt")
        open(path, "w").close()  # lease情報の保存先を作成
        res = runcmd(
            ["dhclient", "-v", "-sf", "/bin/true", "-lf", str(path), "vpn_vpngate"],
            log_disp_out=log_disp_out
        )
        if res.returncode != 0:
            # NIC指定エラーや構文エラーなどはreturncodeが1
            # DHCP取得エラーはreturncodeが0なのでキャッチできない
            print_error(
                "dhclient",
                f"dhclient failed. Error information is below.\n{res.stderr}"
            )
            return (None, None)
        # 情報抽出
        with open(path, "r") as f:
            lease_text = f.read()
        if log_disp_out:
            print_debug(f"DHCP Lease information\n{lease_text}")
        fixed_address_match = re.search(r"fixed-address\s+([\d.]+);", lease_text)
        fixed_address = fixed_address_match.group(1) if fixed_address_match else None
        routers_match = re.search(r"option routers\s+([\d.]+);", lease_text)
        routers = routers_match.group(1) if routers_match else None
        if fixed_address is None or routers is None:
            print_error("ParseDHCPData", "Obtained dhcp data was not valid.")
            if loop:
                continue
        return (fixed_address, routers)


def ipconfig(vpngateip: str):
    # DHCPにてIP取得
    print_log("Obtaining IP Address from vpngate server...")
    (fixed_address, routers) = dhcp()
    fixed_address += "/16"
    print_log(f"Obtained IP: {fixed_address}  GW:{routers}")
    # 上流NICのゲートウェイアドレス取得
    gateway_ip = get_gw(NIC_UPSTREAM)
    # 静的経路設定
    res = runcmd(
        ["ip", "route", "add", vpngateip, "via", gateway_ip, "dev", NIC_UPSTREAM]
    )
    if res.returncode != 0:
        # NIC_UPSTREAMが存在しない場合, gateway_ipやvpngateipが異常の場合1
        # gateway_ipがNexthopとして不適切，すでにvpngateipに対するルートが存在する場合2
        # 発生したらプログラムを続行すべきでない
        print_error(
            "IP Route Add",
            f"ip route add failed. Error information is below.\n{res.stderr}",
        )
        raise FatalErrException()
    # IP設定
    res = runcmd(["ip", "addr", "add", fixed_address, "dev", NIC_VPNGATE])
    if res.returncode != 0:
        print_error(
            "IP Addr Add",
            f"ip addr add failed. Error information is below.\n{res.stderr}",
        )
        raise FatalErrException()
    res = runcmd(["ip", "route", "add", "default", "via", routers, "dev", NIC_VPNGATE])
    if res.returncode != 0:
        print_error(
            "IP Route Add Default",
            f"ip addr add default failed. Error information is below.\n{res.stderr}",
        )
        raise FatalErrException()
    res = runcmd(["curl", "inet-ip.info"])
    if res.returncode != 0:
        print_error(
            "GetWANIP", f"curl failed. Error information is below.\n{res.stderr}"
        )
    print_log(f"IP Configuration OK. WAN IP: {res.stdout}")


def ipreset(vpngateip: str):
    print_log("Resetting IP setting...")
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


def get_bestserver(vpngateip_list: list[str], country: str, port: int) -> str:
    print_log("Getting best vpngate server...")
    server_list = get_server_list(country, port)
    if len(vpngateip_list) != 0:
        # 最後に接続していたサーバと接続失敗サーバは除外
        server_list[:] = [server for server in server_list if server.ip not in vpngateip_list]
    if len(server_list) == 0:
        print_error("GetBestServer", "No server found.")
        # 利用可能なサーバが一つも存在しない場合
        # プログラムを続行すべきでない
        raise FatalErrException()
    print_log(f"Done. Info:{server_list[0]}")
    return server_list[0].get_host()


def vpn_connect(host: str):
    # 接続情報の設定
    print_log("Setting vpngate server address...")
    res = runvpncmd(["accountset", "vpngate", f"/server:{host}", "/hub:vpngate"])
    if errcheck_vpncmd_res(res):
        print_error(
            "VPNCMD_Set",
            f"Accountset command failed. Error information is below.\n{res.stdout}",
        )
        raise FatalErrException()
    # 接続
    print_log("Connecting to vpngate server...")
    res = runvpncmd(["accountconnect", "vpngate"])
    if errcheck_vpncmd_res(res):
        print_error(
            "VPNCMD_Connect",
            f"Connect command failed. Error information is below.\n{res.stdout}",
        )
        raise FatalErrException()
    # 接続状況確認
    retry = 0
    while True:
        retry += 1
        print_log(f"Checking connection... Try:{retry}")
        (valid, status, _) = vpn_status("Session Status")
        if valid and status == "Connection Completed (Session Established)":
            return True  # 接続成功
        elif retry >= 5:
            break
        else:
            time.sleep(1)
    return False  # 接続失敗


def vpn_disconnect():
    # 切断
    print_log("Disconnecting from vpngate server...")
    res = runvpncmd(["accountdisconnect", "vpngate"])
    if errcheck_vpncmd_res(res):
        print_error(
            "VPNCMD_Disconnect",
            f"Disconnect command failed. Error information is below.\n{res.stdout}",
        )
    # 接続状況確認
    print_log("Checking connection...")
    while True:
        (valid, status, _) = vpn_status("Session Status")
        if not valid:
            break
        time.sleep(1)


def runcmd(command: list[str], log_disp_out: bool = True) -> subprocess.CompletedProcess:
    if log_disp_out:
        print_debug(f"RunCMD_args: {' '.join(command)}")
    res = subprocess.run(command, check=False, capture_output=True, text=True)
    if log_disp_out:
        print_debug(f"RunCMD_stdout: {res.stdout}")
        print_debug(f"RunCMD_stderr: {res.stderr}")
    return res


def runvpncmd(command: list[str], log_disp_out: bool = True) -> subprocess.CompletedProcess:
    command = ["vpncmd", "localhost", "/client", "/cmd"] + command
    return runcmd(command, log_disp_out=log_disp_out)


def vpn_status(key: str, log_disp_out: bool = True) -> (bool, str, str):
    res = runvpncmd(["accountstatusget", "vpngate"], log_disp_out=log_disp_out)
    if errcheck_vpncmd_res(res):
        return (False, None, None)
    match = re.search(rf"{re.escape(key)}\s+\|(.+)", res.stdout)
    if match:
        return (True, match.group(1).strip(), res.stdout)
    else:
        return (False, None, res.stdout)


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
            sinfo = ServerConnectInfo(
                s[0],  # hostname
                s[1],  # ip
                get_port_from_openvpn(s[14]),  # port
                str2int(s[2]),  # score
                str2int(s[3]),  # ping
                str2int(s[4]),  # speed
                s[6],  # country
                str2int(s[7]),  # num_vpn_sessions
                str2int(s[8]),  # uptime
                s[12],  # operator
            )
            if country is not None and sinfo.country != country:
                continue
            if port is not None and sinfo.port != port:
                continue
            res.append(sinfo)
            print_debug(repr(sinfo), banner=False)
        res.sort(key=lambda x: x.score, reverse=True)
        return res


def str2int(s: str) -> int:
    if s == '-':
        return None
    try:
        return int(s)
    except Exception as e:
        print_error("str2int", e.message)
        FatalErrException()


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
        unit = ["bps", "kbps", "Mbps", "Gbps", "Tbps", "Pbps"]
        index_unit = 0
        speed = self.speed
        while True:
            if speed >= 1000:
                index_unit += 1
                speed /= 1000
            else:
                break
        return f"{speed:.2f}{unit[index_unit]}"

    def get_uptime(self):
        td = timedelta(seconds=self.uptime)
        m, s = divmod(td.seconds, 60)
        h, m = divmod(m, 60)
        return f"{td.days}d,{h}:{m}:{s}"

    def get_ping(self):
        if self.ping is None:
            return "--"
        else:
            return str(self.ping)

    def get_host(self):
        return f"{self.ip}:{self.port}"

    def __repr__(self):
        return f"{self.hostname} {self.get_host()} ({self.country}) Score:{self.score} Ping:{self.get_ping()}ms Speed:{self.get_speed()} Sessions:{self.num_vpn_sessions} UP:{self.get_uptime()} OP:{self.operator}"


def log_write(msg: str):
    dt = datetime.now(ZoneInfo("Asia/Tokyo"))
    path = Path(__file__).resolve().parent.joinpath(f"log/log-{dt.date()}.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode="a", encoding="utf-8") as f:
        f.write(f"[{dt}] {msg}")


def print_status(msg: str):
    global is_overwrite_active
    if is_overwrite_active:
        sys.stdout.write('\x1b[1A')  # 1行上へ
        sys.stdout.write('\x1b[2K')  # 行をクリア
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()
    is_overwrite_active = True


def print_log(msg: str):
    global is_overwrite_active
    is_overwrite_active = False
    if DEBUG:
        print(f"\033[32m{str(msg)}\033[0m")
    else:
        print(str(msg))
    log_write(f"{str(msg)}\n")


def print_debug(msg, banner=True, end="\n"):
    global is_overwrite_active
    if DEBUG:
        is_overwrite_active = False
        if banner:
            print("\033[45m(DEBUG)\033[0m " + str(msg), end=end)
        else:
            print(str(msg), end=end)
    log_write(f"[DEBUG] {str(msg)}\n")


def print_error(errtype, errmsg):
    global is_overwrite_active
    is_overwrite_active = False
    print(f"\033[31m{str(errtype)}: {str(errmsg)}\033[0m")
    log_write(f"[ERROR] {str(errtype)}: {str(errmsg)}\n")


def err_exit():
    print_log("Terminating due to error...")
    os._exit(1)


def chkroot():
    if os.geteuid() != 0 or os.getuid() != 0:
        print_error("chkroot", "Run As Root!!!")
        err_exit()


class FatalErrException(Exception):
    pass


if __name__ == "__main__":
    os.system("")  # Windowsにて、色付き文字を出力するためのおまじない
    chkroot()
    main()

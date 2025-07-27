import os
import epaper
from PIL import Image, ImageDraw, ImageFont
import time
import logging
import subprocess
import re
import datetime

NIC_UPSTREAM: str = "eth0"

logging.basicConfig(level=logging.DEBUG)
epd = epaper.epaper('epd2in13_V4').EPD()
font32 = ImageFont.truetype(os.path.join('/usr/share/fonts/opentype/ipafont-gothic/', 'ipag.ttf'), 32)
font20 = ImageFont.truetype(os.path.join('/usr/share/fonts/opentype/ipafont-gothic/', 'ipag.ttf'), 20)
font18 = ImageFont.truetype(os.path.join('/usr/share/fonts/opentype/ipafont-gothic/', 'ipag.ttf'), 18)
font14 = ImageFont.truetype(os.path.join('/usr/share/fonts/opentype/ipafont-gothic/', 'ipag.ttf'), 14)


def main():
    try:
        init()
        loop()
    except Exception as e:
        logging.error(e)
        epaper.epaper('epd2in13_V4').epdconfig.module_exit(cleanup=True)


def loop():
    update = True
    ip = ""
    vpn = ""
    while True:
        # Get nic ip
        _ip = get_nicip(NIC_UPSTREAM)
        logging.info(f"{NIC_UPSTREAM}: {_ip}")
        if _ip != ip:
            update = True
            ip = _ip
        # Get vpn status
        _vpn = getvpnstatus()
        logging.info(f"VPN status: {_vpn}")
        if _vpn != vpn:
            update = True
            vpn = _vpn
        if update:
            update = False
            logging.info("Drawing...")
            epd.init()
            image, draw = get_draw()
            draw.text((5, 5), f"{NIC_UPSTREAM}: {ip}", font=font20, fill=0)
            draw.text((5, 35), f"★VPN★\n{vpn}", font=font18, fill=0)
            epd.display(epd.getbuffer(image))
            logging.info("Drawing done.")
            epd.sleep()
            logging.info("Entered sleep mode.")
        else:
            time.sleep(3)


def init():
    logging.info("Init...")
    epd.init()
    image, draw = get_draw()
    draw.text((5, 10), "Init...", font=font32, fill=0)
    epd.display(epd.getbuffer(image))
    epd.sleep()


def get_draw():
    image = Image.new('1', (epd.height, epd.width), 255)  # 255: clear the frame
    draw = ImageDraw.Draw(image)
    dt_now = datetime.datetime.now()
    dtstr = dt_now.strftime("%Y/%m/%d %H:%M:%S")
    draw.text((5, epd.width - 16), f"Last update: {dtstr}", font=font14, fill=0)
    return image, draw


def get_nicip(nic: str) -> (str):
    res = runcmd(
        ["ip", "addr", "show", str(nic)]
    )
    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", res.stdout)
    if match:
        ip = match.group(1)
        ip_part = ip.split(".")
        formatted_ip_part = [part.rjust(3) for part in ip_part]
        return '.'.join(formatted_ip_part)
    else:
        logging.warning("Could not get IP Address of NIC:{nic}")
        return "IP Error"


def runcmd(command: list[str]) -> subprocess.CompletedProcess:
    res = subprocess.run(command, check=False, capture_output=True, text=True)
    return res


def getvpnstatus() -> str:
    command = ["vpncmd", "localhost", "/client", "/cmd", "accountstatusget", "vpngate"]
    res = runcmd(command)
    if res.stdout.rfind("The specified VPN Connection Setting is not connected.") >= 0:
        return "Not connected."
    match = re.search(r"Session Status\s*\|(.+)", res.stdout)
    if match:
        status = match.group(1).strip()
        if status.find("Connection Completed (Session Established)") >= 0:
            status = "Connected."
            match2 = re.search(r"Server Name\s*\|(.+)", res.stdout)
            if match2:
                status += f"\n{match2.group(1).strip()}"
            return status
        else:
            return status
    return "Unknown status."


def chkroot():
    if os.geteuid() != 0 or os.getuid() != 0:
        logging.error("Run As Root!!!")
        os._exit(1)


if __name__ == "__main__":
    os.system("")  # Windowsにて、色付き文字を出力するためのおまじない
    chkroot()
    main()

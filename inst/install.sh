#!/bin/sh
NIC="eth1"
BRANCH="master"
VPNCLIENT="https://github.com/SoftEtherVPN/SoftEtherVPN_Stable/releases/download/v4.44-9807-rtm/softether-vpnclient-v4.44-9807-rtm-2025.04.16-linux-arm64-64bit.tar.gz"

DL() {
  url=$1
  filename=$2
  curl -fsSL -o ${filename} ${url}
  if [ $? -ne 0 ]; then
    echo "Error: Could not download \"${filename}\""
    exit 1
  fi
}


# 引数チェック
# 第一引数: ブランチ
if [ -n "$1" ]; then
  BRANCH=$1
  echo "Set branch to \"${BRANCH}\""
fi


# rootチェック
if [ `id -u` -ne 0 ]; then
  echo "You need to run as root."
  echo
  echo "\"curl -fsSL raw.githubusercontent.com/EngYOSHI/VPNGateRouter/refs/heads/master/inst/install.sh | sudo sh\""
  echo
  exit 1
fi


# 注意事項表示
echo
echo "================================================================================"
echo
echo "VPNGateRouter Installation Script"
echo
echo "Please read the following"
echo "1. This program has been tested only on Raspberry Pi 4 and 5"
echo "   running \"Raspberry Pi OS Lite\"."
echo "2. Before installing this program, please perform a clean installation of"
echo "   the operating system and prepare a fresh environment."
echo "   Do NOT install this program on a system that is already in use."
echo "3. Do NOT run any other programs to this system."
echo "4. Connect a Gigabit LAN USB adapter to the USB 3.0 port before installing."
echo "5. After this installation done, the system will reboot automatically."
echo
echo "================================================================================"
echo
echo -n "Do you want to continue? [y/N]: "

read yn < /dev/tty

if [ "$yn" != "y" ]; then
  echo "Abort."
  exit 1
fi


echo
# eth1の接続チェック
echo -n "USB LAN (${NIC}): "
ip link show ${NIC} > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "NG"
  echo "Error: USB LAN adapter is not connected."
  exit 1
fi
echo "OK"


# aptからパッケージのインストール
echo "Installing packages..."
curl -1sLf 'https://dl.cloudsmith.io/public/isc/kea-2-6/setup.deb.sh' | sudo -E bash
apt -y install kea-dhcp4-server iptables screen isc-dhcp-client git
if [ $? -ne 0 ]; then
  echo "Error: Installing dependencies failed."
  exit 1
fi


echo
echo "Downloading file..."
cd /opt
screen -XS vpngate quit > /dev/null  # screenをタスキル
systemctl stop vpngate-vpncliet 2>/dev/null  # 全部削除するため，念のためvpnclientをタスキル
rm -rf VPNGateRouter
git clone --branch ${BRANCH} https://github.com/EngYOSHI/VPNGateRouter
if [ $? -ne 0 ]; then
  echo "Error: Could not clone VPNGateRouter repo. (${BRANCH} branch)"
  exit 1
fi


echo
echo "Configuring kea-dhcp4-server"
setcap 'cap_net_bind_service,cap_net_raw=+ep' /usr/sbin/kea-dhcp4
cd /opt/VPNGateRouter
cp inst/kea-dhcp4-server-before.sh /usr/local/bin/
chown root:root /usr/local/bin/kea-dhcp4-server-before.sh
chmod 755 /usr/local/bin/kea-dhcp4-server-before.sh
cp systemd/kea-dhcp4-server-before.service /etc/systemd/system/
chown root:root /etc/systemd/system/kea-dhcp4-server-before.service
chmod 755 /etc/systemd/system/kea-dhcp4-server-before.service
systemctl enable kea-dhcp4-server-before.service > /dev/null 2>&1
cp inst/kea-dhcp4.conf /etc/kea/
chmod 755 /etc/kea/
sudo systemctl enable kea-dhcp4-server > /dev/null 2>&1


echo
echo "Installing SoftEther VPNClient..."
DL ${VPNCLIENT} vpnclient.tar.gz
tar -zxf vpnclient.tar.gz
cd /opt/VPNGateRouter/vpnclient
make main > /dev/null
if [ $? -ne 0 ]; then
  echo "Error: Could not install vpnclient"
  exit 1
fi
cd /opt/VPNGateRouter
cp systemd/vpngate-vpnclient.service /etc/systemd/system/
chown root:root /etc/systemd/system/vpngate-vpnclient.service
chmod 755 /etc/systemd/system/vpngate-vpnclient.service
systemctl enable vpngate-vpnclient.service > /dev/null 2>&1
systemctl start vpngate-vpnclient.service
sleep 3


echo
echo "Configuring SoftEther VPNClient..."
cd /opt/VPNGateRouter/vpnclient/
./vpncmd localhost /client /cmd niccreate vpngate
retcode=$?
if [ ${retcode} -ne 0 ] && [ ${retcode} -ne 30 ]; then
  # Status codeが30の場合，既に存在するだけなのでOK
  # それ以外はダメ
  echo "Error: An error occurred while configuring vpnclient."
  exit 1
fi
./vpncmd localhost /client /cmd accountcreate vpngate /server:192.0.2.1:443 /hub:VPNGATE /username:vpn /nicname:vpngate
retcode=$?
if [ ${retcode} -ne 0 ] && [ ${retcode} -ne 34 ]; then
  # Status codeが34の場合，既に存在するだけなのでOK
  # それ以外はダメ
  echo "Error: An error occurred while configuring vpnclient."
  exit 1
fi
./vpncmd localhost /client /cmd accountpasswordset vpngate /password:vpn /type:standard
retcode=$?
if [ ${retcode} -ne 0 ]; then
  echo "Error: An error occurred while configuring vpnclient."
  exit 1
fi
systemctl stop vpngate-vpnclient.service
# vpnclientに設定が保存されないことが頻発するためチェック
if [ ! -f vpn_client.config ] || \
   ! grep -q "HashedPassword H8N7rT8BH44q0nFXC9NlFxetGzQ=" vpn_client.config || \
   ! grep -q "string AccountName vpngate" vpn_client.config || \
   ! grep -q "declare vpngate" vpn_client.config; then
     echo "Error: Config of vpnclient not saved or incorrect."
     exit 1
fi


echo
echo "Configuring VPNGateRouter..."
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-VPNGateRouter.conf
cd /opt/VPNGateRouter
python -m venv venv
sudo venv/bin/pip install requests tzdata dnspython
cp systemd/vpngate-autocon.service /etc/systemd/system/
chown root:root /etc/systemd/system/vpngate-autocon.service
chmod 755 /etc/systemd/system/vpngate-autocon.service
systemctl enable vpngate-autocon.service

echo
echo "Installation done! Rebooting..."
sudo reboot

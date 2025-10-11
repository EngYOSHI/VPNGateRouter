# VPNGateRouter
[VPNGate](https://www.vpngate.net/ja/)によるVPN接続を透過的にデバイスに提供する高可用なルータ
- 簡単なセットアップ
- 自動中継サーバ選択  
  最も最適な中継サーバを自動選択
- 自動フェールオーバー  
  中継サーバから切断時，自動的に復旧
- キルスイッチ機能  
  フェールオーバー中は通信を遮断
- VPN非対応デバイスでも利用可能  
  デバイスをつなぐだけ

## テスト済みハードウェア
✅ Raspberry Pi 4B + Raspberry Pi OS Lite(2025-09-16)  
✅ Raspberry Pi 5 + Raspberry Pi OS Lite(2025-09-16)  

## 使い方
### 準備
1. テスト済みのハードウェアを用意し，指定されたOSを新規インストール
2. 上流ネットワークをハードウェアのLANに接続
3. ギガビットLANアダプタをUSB3.0ポートに接続
4. 以下コマンドを実行  
   インストールが正常終了すると自動的に再起動する  
   再起動後はヘッドレス運用が可能  
   ```
   curl -fsSL raw.githubusercontent.com/EngYOSHI/VPNGateRouter/refs/heads/master/inst/install.sh | sudo sh
   ```
5. LANアダプタにクライアントを接続(IPは動的割り当て)
   - スイッチングハブを介在，あるいは直接有線接続
   - アクセスポイント(ブリッジモード)を介して無線接続

## 依存関係
- `uname -v`
  ```
  #1 SMP PREEMPT Debian 1:6.12.47-1+rpt1 (2025-09-16)
  ```
- `python --version`
  ```
  python 3.13.5
  ```
- `pip3 freeze`
  ```
  requests==2.32.5
  tzdata==2025.2
  dnspython==2.8.0
  ```
- `kea-dhcp4 -v`
  ```
  2.6.3
  ```

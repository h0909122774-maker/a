"""
授權碼產生器（只給作者自己用，不要把這個檔案和 private_key.pem 給別人）

第一次：產生金鑰（會自動把公鑰寫進 licensing.py）
    python keygen.py init

發授權碼給某台電腦：
    python keygen.py A1B2-C3D4-E5F6            # 永久授權
    python keygen.py A1B2-C3D4-E5F6 --days 365 # 一年後到期
    python keygen.py A1B2-C3D4-E5F6 --until 2027-12-31

private_key.pem 一定要備份好：遺失就沒辦法再發授權碼（只能重新 init 並重新打包程式，
舊的授權碼也會全部失效）。
"""

import argparse
import base64
import datetime
import os
import re
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, "private_key.pem")
LICENSING = os.path.join(HERE, "licensing.py")
sys.path.insert(0, HERE)
import licensing  # noqa: E402


def init():
    if os.path.exists(PRIVATE):
        print("private_key.pem 已經存在，不會覆蓋（覆蓋會讓所有舊授權碼失效）。")
        return
    key = Ed25519PrivateKey.generate()
    with open(PRIVATE, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    pub = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    with open(LICENSING, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r'^PUBLIC_KEY = ".*"$', f'PUBLIC_KEY = "{pub}"', src, count=1, flags=re.M)
    with open(LICENSING, "w", encoding="utf-8") as f:
        f.write(src)
    print("已產生 private_key.pem，公鑰已寫入 licensing.py。")
    print("請備份 private_key.pem，並重新打包程式 (build_exe.bat)。")


def issue(code, days=None, until=None):
    with open(PRIVATE, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    code = code.strip().upper()
    if until:
        end = datetime.date.fromisoformat(until)
    elif days:
        end = datetime.date.today() + datetime.timedelta(days=days)
    else:
        end = None
    expiry = int(end.strftime("%Y%m%d")) if end else 0
    sig = key.sign(licensing.message(code, expiry))
    lic = licensing.encode_key(expiry, sig)
    print(f"機器碼：{code}")
    print(f"期限：{end or '永久'}")
    print("授權碼：")
    print(lic)


def main():
    p = argparse.ArgumentParser(description="AutoDim 授權碼產生器")
    p.add_argument("code", help="init 或 機器碼")
    p.add_argument("--days", type=int, help="幾天後到期")
    p.add_argument("--until", help="到期日 YYYY-MM-DD")
    a = p.parse_args()
    if a.code == "init":
        init()
    else:
        issue(a.code, a.days, a.until)


if __name__ == "__main__":
    main()

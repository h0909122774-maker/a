"""
授權檢查：程式只能在「有授權碼」的電腦上執行。

- 每台電腦有一組機器碼（由 Windows 的 MachineGuid 算出來）。
- 授權碼 = 用作者私鑰對「機器碼 + 到期日」做的 Ed25519 簽章。
- 這裡只放公鑰：就算有人看到這個檔案，也做不出新的授權碼。
- 授權碼存在 %APPDATA%\\AutoDim\\license.key

作者用 keygen.py 產生私鑰與授權碼（keygen.py 和私鑰不要給別人）。
"""

import base64
import datetime
import hashlib
import os
import sys

# keygen.py init 會自動把公鑰寫在這裡
PUBLIC_KEY = ""

APP = "AutoDim"


def machine_code():
    """這台電腦的機器碼，例如 A1B2-C3D4-E5F6。"""
    raw = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            raw = winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        import uuid
        raw = f"{uuid.getnode():x}"
    h = hashlib.sha256(f"{raw}|{APP}".encode()).hexdigest().upper()[:12]
    return "-".join(h[i:i + 4] for i in range(0, 12, 4))


def message(code, expiry):
    """被簽章的內容。expiry 是 YYYYMMDD，0 表示永久。"""
    return f"{APP}|{code}|{expiry}".encode()


def encode_key(expiry, signature):
    data = int(expiry).to_bytes(4, "big") + signature
    text = base64.b32encode(data).decode().rstrip("=")
    return "-".join(text[i:i + 5] for i in range(0, len(text), 5))


def decode_key(key):
    text = key.strip().upper().replace("-", "").replace(" ", "")
    text += "=" * (-len(text) % 8)
    data = base64.b32decode(text)
    return int.from_bytes(data[:4], "big"), data[4:]


def verify(key, code=None):
    """回傳 (是否有效, 說明)。"""
    import ed25519_pure
    if not PUBLIC_KEY:
        return False, "程式還沒設定公鑰（作者要先執行 keygen.py init）"
    code = code or machine_code()
    try:
        expiry, sig = decode_key(key)
        good = ed25519_pure.verify(base64.b64decode(PUBLIC_KEY), message(code, expiry), sig)
    except Exception:
        return False, "授權碼格式錯誤"
    if not good:
        return False, "授權碼不正確，或不是這台電腦的授權碼"
    if expiry:
        end = datetime.date(expiry // 10000, expiry // 100 % 100, expiry % 100)
        if datetime.date.today() > end:
            return False, f"授權已於 {end} 到期"
        return True, f"授權有效，到期日 {end}"
    return True, "永久授權"


def license_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP, "license.key")


def check_or_prompt():
    """啟動時檢查授權；沒有或無效就顯示機器碼並要求輸入授權碼。無效則結束程式。"""
    path = license_path()
    code = machine_code()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            ok, msg = verify(f.read(), code)
        if ok:
            print(msg)
            return
        print(msg)
    print("=" * 50)
    print("這台電腦尚未授權。")
    print(f"機器碼：{code}")
    print("請把機器碼傳給作者，取得授權碼後貼在下面。")
    print("=" * 50)
    for _ in range(3):
        try:
            key = input("授權碼：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not key:
            continue
        ok, msg = verify(key, code)
        print(msg)
        if ok:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(key)
            return
    input("未授權，按 Enter 結束。")
    sys.exit(1)

#!/usr/bin/env python3
"""MiniRDP şifresini ayarlar. Kullanım: venv\\Scripts\\python set_password.py [yeni_şifre]"""
import getpass
import hashlib
import json
import secrets
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"


def hash_password(password, salt=None):
    # server.py'deki ile aynı olmalı (server.py içe aktarılmıyor; fare/ekran modüllerini yüklemesin diye).
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return salt, digest


if len(sys.argv) > 1:
    pw = sys.argv[1]
else:
    pw = getpass.getpass("Yeni şifre: ")
    if pw != getpass.getpass("Tekrar: "):
        sys.exit("Şifreler eşleşmiyor.")
if len(pw) < 6:
    sys.exit("Şifre en az 6 karakter olmalı.")
cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
cfg["password_salt"], cfg["password_hash"] = hash_password(pw)
CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
(BASE / "sessions.json").unlink(missing_ok=True)  # eski oturumların hepsini geçersiz kıl
print("Şifre kaydedildi. Değişikliğin geçerli olması için servisi yeniden başlatın (yönetici PowerShell):")
print("  Stop-ScheduledTask MiniRDP; Start-ScheduledTask MiniRDP")

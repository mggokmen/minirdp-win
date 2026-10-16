#!/usr/bin/env python3
"""Yerel CA (yoksa) ve bu bilgisayarın IP'si için sunucu sertifikası üretir.

Kullanım: venv\\Scripts\\python scripts\\make_certs.py [IP]   (IP verilmezse otomatik bulunur)
"""
import datetime as dt
import ipaddress
import re
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERTS = Path(__file__).resolve().parent.parent / "certs"


def local_ip():
    # Varsayılan rotadaki arayüzün adresi (UDP connect paket göndermez).
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]


def write_key(path, key):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def main():
    ip = ipaddress.ip_address(sys.argv[1] if len(sys.argv) > 1 else local_ip())
    host = socket.gethostname().lower()
    host = host if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host) else None
    CERTS.mkdir(exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)

    ca_key_path, ca_crt_path = CERTS / "ca.key", CERTS / "minirdp-ca.crt"
    if ca_key_path.exists():
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), None)
        ca_crt = x509.load_pem_x509_certificate(ca_crt_path.read_bytes())
    else:
        ca_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, f"MiniRDP Yerel CA ({host or ip})"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MiniRDP"),
        ])
        # CA yalnızca yerel ağ adresleri ve .local adları için sertifika imzalayabilir.
        permitted = [x509.IPAddress(ipaddress.ip_network(n)) for n in
                     ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "127.0.0.0/8")]
        permitted += [x509.DNSName("local"), x509.DNSName("localhost")]
        ca_crt = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        write_key(ca_key_path, ca_key)
        ca_crt_path.write_bytes(ca_crt.public_bytes(serialization.Encoding.PEM))
        print("Yeni CA oluşturuldu - bağlanacak bilgisayara minirdp-ca.crt kurulmalı.")

    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.IPAddress(ip), x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]
    if host:
        san.append(x509.DNSName(f"{host}.local"))
    crt = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(ip))]))
        .issuer_name(ca_crt.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    write_key(CERTS / "server.key", key)
    (CERTS / "server.crt").write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    print(f"Sunucu sertifikası: {ip}" + (f", {host}.local" if host else ""))
    print("CA parmak izi (Thumbprint):", ca_crt.fingerprint(hashes.SHA1()).hex().upper())


if __name__ == "__main__":
    main()

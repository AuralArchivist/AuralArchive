"""
Module Name: voucher.py
Author: AuralArchivist
Created: September 25, 2026
Last Modified: October 9, 2026
Description:
    Decrypts the license_response in an Audible AAXC voucher to recover the
    audible_key / audible_iv needed to decrypt the audio file.

Location:
    /integrations/audible/voucher.py
"""

import base64
import json
import re
from hashlib import sha256
from typing import Any, Dict

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def decrypt_license_response(auth: Any, asin: str, encrypted_voucher: str) -> Dict[str, str]:
    """Return {'key': ..., 'iv': ...} from an encrypted license_response.

    Only works with the Audible sign-in (device) the license was issued to.
    """
    device_info = getattr(auth, 'device_info', None) or {}
    customer_info = getattr(auth, 'customer_info', None) or {}

    device_serial = device_info.get('device_serial_number')
    device_type = device_info.get('device_type')
    customer_id = customer_info.get('user_id')

    if not all([device_serial, device_type, customer_id]):
        raise ValueError("Authenticator missing device/customer metadata for voucher decryption")

    try:
        buffer = (device_type + device_serial + customer_id + asin).encode('ascii')
    except UnicodeEncodeError as exc:
        raise ValueError(f"Failed to encode voucher derivation data: {exc}")

    digest = sha256(buffer).digest()
    aes_key = digest[:16]
    aes_iv = digest[16:]

    try:
        ciphertext = base64.b64decode(encrypted_voucher)
    except Exception as exc:
        raise ValueError(f"Voucher payload is not valid base64: {exc}")

    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv), backend=default_backend())
    decryptor = cipher.decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    plaintext = plaintext.rstrip(b"\x00")

    try:
        decoded = plaintext.decode('utf-8')
    except UnicodeDecodeError:
        decoded = plaintext.decode('utf-8', errors='ignore')

    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        match = re.match(r'^\{"key":"(?P<key>[^"\\]+)","iv":"(?P<iv>[^"\\]+)"', decoded)
        if match:
            payload = match.groupdict()
        else:
            raise ValueError("Unable to parse decrypted voucher payload (voucher was issued to a different Audible sign-in)")

    if 'key' not in payload or 'iv' not in payload:
        raise ValueError("Decrypted voucher payload missing key/iv fields")

    return {'key': payload['key'], 'iv': payload['iv']}

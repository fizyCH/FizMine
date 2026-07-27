"""Authentication primitives shared by the panel application."""
import hashlib, hmac, os

def password_hash(password, salt=None):
    salt=salt or os.urandom(16)
    digest=hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex()+"$"+digest.hex()

def password_matches(password, stored):
    try:
        salt,digest=stored.split("$",1)
        return hmac.compare_digest(password_hash(password, bytes.fromhex(salt)).split("$",1)[1], digest)
    except (ValueError, AttributeError):
        return False

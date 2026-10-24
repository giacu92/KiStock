"""Windows per-user encrypted credentials, kept outside project/package files."""
import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Persistent credentials currently require Windows. Leave Remember token unchecked on other systems.')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = library.CryptUnprotectData
    else:
        function = library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError('Windows could not decrypt the saved credential.' if decrypt else 'Windows could not encrypt the credential.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def credential_key(service, url):
    return service + ':' + url.strip().rstrip('/')


def read_store(settings_file):
    path = Path(settings_file).with_name('credentials.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def load_secret(settings_file, key):
    encrypted = read_store(settings_file).get(key)
    return crypt(base64.b64decode(encrypted), decrypt=True).decode('utf-8') if encrypted else ''


def save_secret(settings_file, key, value):
    store = read_store(settings_file)
    if value:
        store[key] = base64.b64encode(crypt(value.encode('utf-8'))).decode('ascii')
    else:
        store.pop(key, None)
    path = Path(settings_file).with_name('credentials.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(store, indent=2), encoding='utf-8')
    temporary.replace(path)

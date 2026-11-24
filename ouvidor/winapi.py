"""Chamadas ao Windows via ctypes: pastas conhecidas, DPAPI e endpoints de áudio."""

import base64
import ctypes
import uuid
from ctypes import POINTER, byref, c_byte, c_void_p, wintypes
from pathlib import Path

_ole32 = ctypes.WinDLL("ole32")
_shell32 = ctypes.WinDLL("shell32")
_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_ole32.CoTaskMemFree.argtypes = [c_void_p]
_ole32.CoCreateInstance.restype = ctypes.HRESULT
_kernel32.LocalFree.argtypes = [c_void_p]


def _guid(text: str):
    return (c_byte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)


def documents_dir() -> Path:
    """Pasta Documentos real, mesmo quando redirecionada para o OneDrive."""
    ptr = c_void_p()
    fid = _guid("FDD39AD0-238F-46AF-ADB4-6C85480369C7")
    if _shell32.SHGetKnownFolderPath(byref(fid), 0, None, byref(ptr)) != 0:
        return Path.home() / "Documents"
    try:
        return Path(ctypes.wstring_at(ptr.value))
    finally:
        _ole32.CoTaskMemFree(ptr)


# --- DPAPI -----------------------------------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", c_void_p)]


def _dpapi(func, data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, c_void_p))
    out = _Blob()
    if not func(byref(src), None, None, None, None, 0x1, byref(out)):
        raise OSError(ctypes.get_last_error(), "falha na DPAPI")
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        _kernel32.LocalFree(out.data)


def protect(text: str) -> str:
    """Cifra um texto para que só este usuário do Windows consiga ler."""
    return base64.b64encode(_dpapi(_crypt32.CryptProtectData, text.encode())).decode()


def unprotect(token: str) -> str:
    return _dpapi(_crypt32.CryptUnprotectData, base64.b64decode(token)).decode()


# --- Endpoints de áudio (IMMDeviceEnumerator) ------------------------------

def _vtable(obj):
    return ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents


def _call(obj, index, argtypes, *args):
    func = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(_vtable(obj)[index])
    return func(obj, *args)


def _release(obj):
    ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(_vtable(obj)[2])(obj)


def _device_id(device) -> str:
    ptr = c_void_p()
    _call(device, 5, [POINTER(c_void_p)], byref(ptr))
    try:
        return ctypes.wstring_at(ptr.value)
    finally:
        _ole32.CoTaskMemFree(ptr)


def audio_fingerprint() -> tuple:
    """Muda quando o dispositivo padrão troca ou um dispositivo entra ou sai."""
    initialized = _ole32.CoInitializeEx(None, 0) >= 0
    try:
        return _read_endpoints()
    finally:
        if initialized:
            _ole32.CoUninitialize()


def _read_endpoints() -> tuple:
    enum = c_void_p()
    clsid = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
    iid = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
    _ole32.CoCreateInstance(byref(clsid), None, 1, byref(iid), byref(enum))
    result = []
    try:
        for flow in (0, 1):  # saída, entrada
            dev = c_void_p()
            try:
                _call(enum, 4, [ctypes.c_int, ctypes.c_int, POINTER(c_void_p)], flow, 0, byref(dev))
                result.append(_device_id(dev))
                _release(dev)
            except OSError:
                result.append(None)
        coll = c_void_p()
        _call(enum, 3, [ctypes.c_int, wintypes.DWORD, POINTER(c_void_p)], 2, 1, byref(coll))
        try:
            count = wintypes.UINT()
            _call(coll, 3, [POINTER(wintypes.UINT)], byref(count))
            ids = []
            for i in range(count.value):
                dev = c_void_p()
                _call(coll, 4, [wintypes.UINT, POINTER(c_void_p)], i, byref(dev))
                ids.append(_device_id(dev))
                _release(dev)
            result.append(tuple(sorted(ids)))
        finally:
            _release(coll)
    finally:
        _release(enum)
    return tuple(result)

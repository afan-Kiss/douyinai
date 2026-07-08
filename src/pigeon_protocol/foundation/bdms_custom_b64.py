"""Custom base64 — port of jsvmp fn#130 (bdms v1.0.1.20)."""
from __future__ import annotations

# Alphabets from string pool s0-s4
ALPHABETS = {
    "s0": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
    "s1": "Dkdpgh4ZKsQB80/Mfvw36XI1R25+WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe",
    "s2": "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe",
    "s3": "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe",
    "s4": "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe",
    "final": "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe",
}


def custom_b64_encode(data: bytes | str, alphabet_key: str = "s4") -> str:
    """fn#130: 3-byte → 4-char custom base64."""
    if isinstance(data, str):
        raw = data.encode("latin-1", errors="surrogateescape")
    else:
        raw = bytes(data)
    alpha = ALPHABETS.get(alphabet_key, ALPHABETS["s4"])
    pad = ALPHABETS["s0"][0]  # '=' stored at pool 246
    out: list[str] = []
    i = 0
    n = len(raw)
    while i + 3 <= n:
        b0, b1, b2 = raw[i], raw[i + 1], raw[i + 2]
        v = (b0 << 16) | (b1 << 8) | b2
        out.append(alpha[(v >> 18) & 63])
        out.append(alpha[(v >> 12) & 63])
        out.append(alpha[(v >> 6) & 63])
        out.append(alpha[v & 63])
        i += 3
    rem = n - i
    if rem == 0:
        return "".join(out)
    if rem == 1:
        b0 = raw[i]
        v = b0 << 16
        out.append(alpha[(v >> 18) & 63])
        out.append(alpha[(v >> 12) & 63])
        out.append(pad)
        out.append(pad)
    else:
        b0, b1 = raw[i], raw[i + 1]
        v = (b0 << 16) | (b1 << 8)
        out.append(alpha[(v >> 18) & 63])
        out.append(alpha[(v >> 12) & 63])
        out.append(alpha[(v >> 6) & 63])
        out.append(pad)
    return "".join(out)


def rc4(data: bytes, key: bytes) -> bytes:
  out = bytearray(len(data))
  s = list(range(256))
  j = 0
  for i in range(256):
    j = (j + s[i] + key[i % len(key)]) % 256
    s[i], s[j] = s[j], s[i]
  i = j = 0
  for k, b in enumerate(data):
    i = (i + 1) % 256
    j = (j + s[i]) % 256
    s[i], s[j] = s[j], s[i]
    out[k] = b ^ s[(s[i] + s[j]) % 256]
  return bytes(out)

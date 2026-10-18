#!/usr/bin/python3 -I
"""Minimal RDP connection probe (no credentials): X.224 Connection Request with RDP_NEG_REQ, then
optionally TLS. usage: rdpprobe.py HOST PORT [--proto ssl|rdp|hybrid] [--tls-max 1.0|1.1|1.2|1.3]
[--stall SECONDS]. Prints JSON: negotiation result, TLS version, cipher, certificate SHA-256."""
import hashlib, json, socket, ssl, struct, sys, time

def x224_cr(proto_bits):
    neg = struct.pack("<BBHI", 0x01, 0x00, 8, proto_bits)  # RDP_NEG_REQ
    cookie = b"Cookie: mstshash=mios-probe\r\n"
    x224 = bytes([len(cookie) + len(neg) + 6, 0xE0, 0, 0, 0, 0, 0]) + cookie + neg
    return struct.pack(">BBH", 3, 0, 4 + len(x224)) + x224

def main():
    a = sys.argv[1:]
    host, port = a[0], int(a[1])
    proto = {"rdp": 0, "ssl": 1, "hybrid": 3}[a[a.index("--proto") + 1] if "--proto" in a else "ssl"]
    tls_max = a[a.index("--tls-max") + 1] if "--tls-max" in a else None
    stall = float(a[a.index("--stall") + 1]) if "--stall" in a else 0
    out = {"requested": proto}
    s = socket.create_connection((host, port), timeout=10)
    t0 = time.time()
    try:
        s.sendall(x224_cr(proto))
        hdr = s.recv(4)
        if len(hdr) < 4:
            out["result"] = "closed before X.224 confirm"
            out["closed_after_s"] = round(time.time() - t0, 3)
            print(json.dumps(out)); return
        n = struct.unpack(">H", hdr[2:4])[0]
        body = b""
        while len(body) < n - 4:
            c = s.recv(n - 4 - len(body))
            if not c:
                break
            body += c
        neg = body[-8:]
        typ, _, _, val = struct.unpack("<BBHI", neg)
        if typ == 0x02:
            out["result"] = "negotiation response"
            out["selected"] = val
        elif typ == 0x03:
            out["result"] = "negotiation failure"
            out["failure_code"] = val  # 1 = SSL_REQUIRED_BY_SERVER, 2 = SSL_NOT_ALLOWED, 5 = HYBRID_REQUIRED
            print(json.dumps(out)); return
        if proto & 1 and out.get("selected") in (1, 2):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            if tls_max:
                v = {"1.0": ssl.TLSVersion.TLSv1, "1.1": ssl.TLSVersion.TLSv1_1, "1.2": ssl.TLSVersion.TLSv1_2,
                     "1.3": ssl.TLSVersion.TLSv1_3}[tls_max]
                ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
                ctx.maximum_version = v
                try:
                    ctx.set_ciphers("ALL:@SECLEVEL=0")
                except ssl.SSLError:
                    pass
            try:
                t = ctx.wrap_socket(s, server_hostname=None)
                out["tls_version"] = t.version()
                out["cipher"] = t.cipher()[0]
                out["cert_sha256"] = hashlib.sha256(t.getpeercert(binary_form=True)).hexdigest()
                if stall:
                    t.settimeout(stall + 40)
                    t1 = time.time()
                    try:
                        d = t.recv(1)
                        out["stall_result"] = "closed by server" if not d else "data"
                    except (socket.timeout, TimeoutError):
                        out["stall_result"] = "still open"
                    except (ssl.SSLError, OSError) as e:
                        out["stall_result"] = f"closed by server ({e.__class__.__name__})"
                    out["stall_closed_after_s"] = round(time.time() - t1, 1)
            except (ssl.SSLError, OSError) as e:
                out["tls_error"] = str(e)
    finally:
        s.close()
    print(json.dumps(out))

main()

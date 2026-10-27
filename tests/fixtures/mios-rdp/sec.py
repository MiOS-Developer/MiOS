#!/usr/bin/python3 -I
"""A1/A3/A4/A5/A6 security tests against a private native server (test-auth build)."""
import hashlib, json, os, re, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402

H = e2e.HERE
PROBE = [sys.executable, "-I", f"{H}/rdpprobe.py", "127.0.0.1", "18301"]

def probe(*args, timeout=60):
    r = subprocess.run([*PROBE, *args], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": r.stdout + r.stderr}

def cert_sha256(path):
    der = subprocess.run(["openssl", "x509", "-in", path, "-outform", "DER"], capture_output=True, check=True).stdout
    return hashlib.sha256(der).hexdigest()

def client_run(s, args, secs=8, **kw):
    c = s.start_client(args, **kw)
    try:
        c.wait(secs)
    except subprocess.TimeoutExpired:
        c.send_signal(2)
        c.wait(5)
    return c.returncode

def log(s):
    return open(f"{s.out}/server.log").read()

def main():
    d = os.environ.get("MIOS_RDP_LIVE_DIR") or tempfile.mkdtemp(prefix="mios-rdp-sec-")
    e2e.ensure_cert(d)
    res = {}
    before = e2e.guards()
    # --- server 1: loopback allowed
    e2e.write_config(d)
    s = e2e.Session(d, "sec1")
    try:
        s.start_server()
        s.start_xvfb()
        p = probe("--proto", "ssl")
        res["A3_tls"] = {"tls_version": p.get("tls_version"), "cipher": p.get("cipher"),
                         "cert_matches": p.get("cert_sha256") == cert_sha256(f"{d}/cert.pem")}
        res["A3_rdp_security_refused"] = probe("--proto", "rdp")
        res["A3_tls10_refused"] = probe("--proto", "ssl", "--tls-max", "1.0")
        res["A3_tls11_refused"] = probe("--proto", "ssl", "--tls-max", "1.1")
        res["A3_tls12"] = probe("--proto", "ssl", "--tls-max", "1.2")
        # A6: a peer that finishes TLS and then stalls (never sends MCS/Client Info)
        st = subprocess.Popen([*PROBE, "--proto", "ssl", "--stall", "40"], stdout=subprocess.PIPE, text=True)
        time.sleep(3)
        mid = e2e.guards()
        # A5 (pre-auth): a second connection while the stalled one holds the slot is refused at once
        p2 = probe("--proto", "ssl")
        stall = json.loads(st.communicate(timeout=90)[0].strip().splitlines()[-1])
        L = log(s)
        res["A6_stalled_peer"] = {"no_output_while_stalled": not mid["scaletest"],
                                  "no_capture_before_auth": "capture session created" not in L and "remote output attached" not in L,
                                  "stall": stall}
        res["A5_second_while_negotiating"] = {"probe": p2, "logged_reject": "refused: a session is live" in L}
        # A1: wrong password, then the right one inside the backoff, then after it
        rc_bad = client_run(s, ["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard"], password="definitely-wrong", log_name="client-bad.log")
        rc_back = client_run(s, ["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard"], secs=6, log_name="client-backoff.log")
        time.sleep(2.5)
        c_ok = s.start_client(["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard"], log_name="client-ok.log")
        att = s.wait_attached(15)
        c_ok.send_signal(2)
        c_ok.wait(10)
        time.sleep(1.5)
        rc_empty = client_run(s, ["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard"], password="", log_name="client-empty.log")
        L = log(s)
        res["A1"] = {"wrong_password_refused": "refused by PAM/group check" in L or "Credential validation rejected" in L,
                     "wrong_rc": rc_bad,
                     "backoff_refused": "refused: authentication backoff" in L, "backoff_rc": rc_back,
                     "correct_after_backoff_attached": bool(att),
                     "empty_refused": "refused: empty credentials" in L, "empty_rc": rc_empty,
                     "authenticated_lines": len(re.findall(r"authenticated peer=", L))}
    finally:
        s.stop()
    # --- server 2: loopback NOT allowed (A4)
    e2e.write_config(d, allow_loopback=False)
    s2 = e2e.Session(d, "sec2")
    try:
        s2.start_server()
        t0 = time.time()
        p = probe("--proto", "ssl")
        res["A4_source_refused"] = {"probe": p, "seconds": round(time.time() - t0, 3),
                                    "logged": "refused before X.224" in log(s2)}
    finally:
        s2.stop()
    after = e2e.guards()
    res["guards_ok"] = before["portal"] == after["portal"] and after["watchdog"] == "inactive" \
        and before["local"] == after["local"] and not after["scaletest"]
    json.dump(res, open(f"{d}/sec.json", "w"), indent=1)
    print(json.dumps(res, indent=1))

main()

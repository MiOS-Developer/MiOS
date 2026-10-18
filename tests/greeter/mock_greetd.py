#!/usr/bin/env python3
# AI-hint: Mock greetd IPC server for testing the MiOS glass greeter without touching the real greetd.
# Speaks the greetd JSON protocol (u32 native-endian length + JSON) on a private unix socket:
# create_session / post_auth_message_response / start_session / cancel_session. Every request and
# reply is appended to a JSON-lines transcript so tests can assert on what the greeter sent.
# AI-related: ../test-greeter.py, usr/share/mios/greeter/shell.qml
"""Usage: mock_greetd.py SOCKET TRANSCRIPT [--user NAME=PASSWORD ...] [--otp NAME=CODE] [--info NAME=TEXT]"""
import argparse
import json
import os
import socket
import struct
import sys


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("socket")
    ap.add_argument("transcript")
    ap.add_argument("--user", action="append", default=[], help="NAME=PASSWORD")
    ap.add_argument("--otp", action="append", default=[], help="NAME=CODE (second, visible prompt)")
    ap.add_argument("--info", action="append", default=[], help="NAME=TEXT (info message before the password)")
    a = ap.parse_args()
    users = dict(u.split("=", 1) for u in a.user)
    otps = dict(u.split("=", 1) for u in a.otp)
    infos = dict(u.split("=", 1) for u in a.info)

    try:
        os.unlink(a.socket)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(a.socket)
    os.chmod(a.socket, 0o600)
    srv.listen(4)
    log = open(a.transcript, "a", buffering=1)

    def note(direction, obj):
        log.write(json.dumps({"dir": direction, "msg": obj}) + "\n")

    while True:
        conn, _ = srv.accept()
        state = {"user": None, "steps": [], "ok": False, "answers": []}

        def send(obj):
            note("out", obj)
            data = json.dumps(obj).encode()
            conn.sendall(struct.pack("=I", len(data)) + data)

        def next_step():
            if state["steps"]:
                kind, text = state["steps"][0]
                send({"type": "auth_message", "auth_message_type": kind, "auth_message": text})
            else:
                state["ok"] = True
                send({"type": "success"})

        while True:
            hdr = recv_exact(conn, 4)
            if hdr is None:
                note("eof", {})
                break
            (n,) = struct.unpack("=I", hdr)
            body = recv_exact(conn, n)
            if body is None:
                break
            req = json.loads(body)
            note("in", req)
            t = req.get("type")
            if t == "create_session":
                if state["user"] is not None:
                    send({"type": "error", "error_type": "error", "description": "a session is already being configured"})
                    continue
                user = req.get("username", "")
                steps = []
                if user in infos:
                    steps.append(("info", infos[user]))
                steps.append(("secret", "Password:"))
                if user in otps:
                    steps.append(("visible", "One-time code:"))
                state.update(user=user, steps=steps, ok=False, answers=[])
                next_step()
            elif t == "post_auth_message_response":
                if not state["steps"]:
                    send({"type": "error", "error_type": "error", "description": "no auth message pending"})
                    continue
                kind, _text = state["steps"].pop(0)
                if kind in ("secret", "visible"):
                    state["answers"].append(req.get("response"))
                if not state["steps"]:
                    user = state["user"]
                    want = [users.get(user)] + ([otps[user]] if user in otps else [])
                    if user not in users or state["answers"] != want:
                        state.update(user=None, steps=[], ok=False, answers=[])
                        send({"type": "error", "error_type": "auth_error", "description": "pam_authenticate: AUTH_ERR"})
                        continue
                next_step()
            elif t == "start_session":
                if not state["ok"]:
                    send({"type": "error", "error_type": "error", "description": "session not authenticated"})
                    continue
                send({"type": "success"})
            elif t == "cancel_session":
                state.update(user=None, steps=[], ok=False, answers=[])
                send({"type": "success"})
            else:
                send({"type": "error", "error_type": "error", "description": f"unknown request {t}"})
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

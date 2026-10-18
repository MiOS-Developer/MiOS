#!/usr/bin/env python3
# AI-hint: Test-only OCI distribution registry (in-memory, plain HTTP, loopback) just large enough for skopeo to push an image plus its sigstore attachment and pull both back; used by tests/test-cosign-trust-anchor.sh inside a private network namespace.
# AI-related: tests/test-cosign-trust-anchor.sh, automation/49-cosign-policy.sh
"""Usage: oci-mini-registry.py PORT READY_FILE"""
import hashlib
import http.server
import json
import re
import sys
import uuid

BLOBS: dict[str, bytes] = {}
MANIFESTS: dict[tuple[str, str], tuple[bytes, str]] = {}  # (repo, ref) -> (body, media type)
UPLOADS: dict[str, bytearray] = {}

R_UPLOAD = re.compile(r"^/v2/(?P<repo>.+)/blobs/uploads/(?P<id>[^/?]*)$")
R_BLOB = re.compile(r"^/v2/(?P<repo>.+)/blobs/(?P<digest>sha256:[0-9a-f]{64})$")
R_MANIFEST = re.compile(r"^/v2/(?P<repo>.+)/manifests/(?P<ref>[^/]+)$")


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        self.send_header("Docker-Distribution-API-Version", "registry/2.0")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _err(self, code, what):
        self._send(code, json.dumps({"errors": [{"code": what, "message": what.lower()}]}).encode(),
                   {"Content-Type": "application/json"})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _split(self):
        path, _, query = self.path.partition("?")
        params = dict(p.partition("=")[::2] for p in query.split("&") if p)
        return path, params

    def do_GET(self):
        path, _ = self._split()
        if path in ("/v2", "/v2/"):
            return self._send(200, b"{}", {"Content-Type": "application/json"})
        m = R_BLOB.match(path)
        if m:
            b = BLOBS.get(m["digest"])
            if b is None:
                return self._err(404, "BLOB_UNKNOWN")
            return self._send(200, b, {"Content-Type": "application/octet-stream", "Docker-Content-Digest": m["digest"]})
        m = R_MANIFEST.match(path)
        if m:
            hit = MANIFESTS.get((m["repo"], m["ref"]))
            if hit is None:
                return self._err(404, "MANIFEST_UNKNOWN")
            body, mt = hit
            return self._send(200, body, {"Content-Type": mt,
                                          "Docker-Content-Digest": "sha256:" + hashlib.sha256(body).hexdigest()})
        return self._err(404, "NAME_UNKNOWN")

    do_HEAD = do_GET

    def do_POST(self):
        path, params = self._split()
        m = R_UPLOAD.match(path)
        if not m:
            return self._err(404, "NAME_UNKNOWN")
        data = self._body()
        if params.get("digest"):
            return self._finish(m["repo"], bytearray(data), params["digest"].replace("%3A", ":"))
        uid = uuid.uuid4().hex
        UPLOADS[uid] = bytearray(data)
        self._send(202, b"", {"Location": f"/v2/{m['repo']}/blobs/uploads/{uid}", "Range": "0-0",
                              "Docker-Upload-UUID": uid})

    def do_PATCH(self):
        path, _ = self._split()
        m = R_UPLOAD.match(path)
        if not m or m["id"] not in UPLOADS:
            return self._err(404, "BLOB_UPLOAD_UNKNOWN")
        buf = UPLOADS[m["id"]]
        buf.extend(self._body())
        self._send(202, b"", {"Location": f"/v2/{m['repo']}/blobs/uploads/{m['id']}",
                              "Range": f"0-{max(len(buf) - 1, 0)}", "Docker-Upload-UUID": m["id"]})

    def _finish(self, repo, buf, digest):
        if "sha256:" + hashlib.sha256(buf).hexdigest() != digest:
            return self._err(400, "DIGEST_INVALID")
        BLOBS[digest] = bytes(buf)
        self._send(201, b"", {"Location": f"/v2/{repo}/blobs/{digest}", "Docker-Content-Digest": digest})

    def do_PUT(self):
        path, params = self._split()
        m = R_UPLOAD.match(path)
        if m:
            buf = UPLOADS.pop(m["id"], None)
            if buf is None:
                return self._err(404, "BLOB_UPLOAD_UNKNOWN")
            buf.extend(self._body())
            return self._finish(m["repo"], buf, params.get("digest", "").replace("%3A", ":"))
        m = R_MANIFEST.match(path)
        if m:
            body = self._body()
            mt = self.headers.get("Content-Type", "application/vnd.oci.image.manifest.v1+json")
            digest = "sha256:" + hashlib.sha256(body).hexdigest()
            MANIFESTS[(m["repo"], m["ref"])] = (body, mt)
            MANIFESTS[(m["repo"], digest)] = (body, mt)
            return self._send(201, b"", {"Location": f"/v2/{m['repo']}/manifests/{digest}",
                                         "Docker-Content-Digest": digest})
        return self._err(404, "NAME_UNKNOWN")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # containers/image probes HTTPS first on an insecure registry; that reset is expected


def main():
    port, ready = int(sys.argv[1]), sys.argv[2]
    srv = Server(("127.0.0.1", port), H)
    open(ready, "w").write("ready\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()

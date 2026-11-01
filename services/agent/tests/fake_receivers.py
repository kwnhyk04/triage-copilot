"""Fake Discord and Gmail for test_alerting_int.py, each run in the Python image on a network with
no route out: `python fake_receivers.py discord|smtp CERT KEY`. Each request it takes is appended
to /tmp/received.jsonl as one JSON object, which the test reads back with docker exec.
"""

import base64
import json
import socket
import socketserver
import ssl
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

RECEIVED = Path("/tmp/received.jsonl")  # noqa: S108 - the fake's own container


def record(entry: dict[str, Any]) -> None:
    with RECEIVED.open("a", encoding="utf-8") as received:
        received.write(json.dumps(entry) + "\n")


class Discord(BaseHTTPRequestHandler):
    """Discord's webhook route: 204 to every POST, as Discord answers one without ?wait. HTTP/1.0,
    http.server's default, so each answer closes its connection and the proxy logs the tunnel."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        record({"path": self.path, "body": self.rfile.read(length).decode()})
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: the test reads RECEIVED."""


class Smtp(socketserver.StreamRequestHandler):
    """Enough of Gmail's submission port for Alertmanager: STARTTLS, AUTH PLAIN, then messages."""

    context: ssl.SSLContext

    def handle(self) -> None:
        conn: socket.socket = self.connection
        reader = conn.makefile("rb")
        tls = False
        entry: dict[str, Any] = {}

        def send(*lines: str) -> None:
            conn.sendall("".join(f"{line}\r\n" for line in lines).encode())

        send("220 smtp.gmail.com ESMTP fake")
        while line := reader.readline():
            text = line.decode().rstrip("\r\n")
            verb = text.split(" ", 1)[0].upper()
            if verb in {"EHLO", "HELO"}:
                send("250-smtp.gmail.com", "250 AUTH PLAIN" if tls else "250 STARTTLS")
            elif verb == "STARTTLS" and not tls:
                send("220 2.0.0 Ready to start TLS")
                conn = self.context.wrap_socket(conn, server_side=True)
                reader = conn.makefile("rb")
                tls = True
            elif verb == "AUTH" and tls:
                _, mechanism, encoded = text.split(" ", 2)
                _, user, password = base64.b64decode(encoded).decode().split("\0")
                entry |= {"mechanism": mechanism, "user": user, "password": password}
                send("235 2.7.0 Accepted")
            elif verb == "MAIL":
                entry["from"] = text
                send("250 2.1.0 OK")
            elif verb == "RCPT":
                entry.setdefault("to", []).append(text)
                send("250 2.1.5 OK")
            elif verb == "DATA":
                send("354 Go ahead")
                data = bytearray()
                while (chunk := reader.readline()) not in (b".\r\n", b""):
                    data += chunk[1:] if chunk.startswith(b"..") else chunk
                record({**entry, "tls": tls, "data": data.decode()})
                send("250 2.0.0 OK")
            elif verb == "QUIT":
                send("221 2.0.0 Bye")
                return
            else:
                send("502 5.5.2 Not here")


def main() -> None:
    kind, cert, key = sys.argv[1:]
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    if kind == "discord":
        server = HTTPServer(("0.0.0.0", 443), Discord)  # noqa: S104 - the fake's own network
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.serve_forever()
    Smtp.context = context
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", 587), Smtp) as smtp:  # noqa: S104
        smtp.serve_forever()


if __name__ == "__main__":
    main()

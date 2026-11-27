#!/usr/bin/env -S uv run --no-project --isolated --with httpx python
"exec" "sh" "-c" "/usr/bin/env -S uv run --no-project --isolated --with httpx python $0 $@"

"""
alpaca proxy to stop NINA from hammering the Seestar
"""

import argparse
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("alpaca-proxy")

# Endpoints whose values never change while connected
STATIC_ENDPOINTS = {
    "name", "description", "driverinfo", "driverversion", "interfaceversion",
    "pixelsizex", "pixelsizey", "electronsperadu", "exposuremin", "exposuremax",
    "exposureresolution", "maxbinx", "maxbiny", "cameraxsize", "cameraysize",
    "maxadu", "fullwellcapacity", "sensorname", "sensortype",
    "bayeroffsetx", "bayeroffsety",
    "cangetcoolerpower", "cansetccdtemperature", "canabortexposure",
    "canstopexposure", "canpulseguide", "canfastreadout", "canasymmetricbin",
    "gainmin", "gainmax", "gains", "offsetmin", "offsetmax", "offsets",
    "readoutmodes", "supportedactions",
}

# Status/telemetry endpoints safe to serve from a short TTL cache
TTL_ENDPOINTS = {
    "connected", "camerastate", "ccdtemperature", "heatsinktemperature",
    "cooleron", "coolerpower", "setccdtemperature",
    "gain", "offset", "readoutmode", "binx", "biny",
    "startx", "starty", "numx", "numy", "fastreadout",
    "subexposureduration", "lastexposureduration", "lastexposurestarttime",
}


class SerializedAlpacaProxy:
    def __init__(
        self,
        target_host: str,
        target_port: int,
        cooldown_ms: int,
        timeout_s: float,
        cache_ttl_ms: int
    ):
        self.target_base = f"http://{target_host}:{target_port}"
        self.cooldown_s = cooldown_ms / 1000.0
        self.cache_ttl_s = cache_ttl_ms / 1000.0
        self.client = httpx.Client(base_url=self.target_base, timeout=timeout_s)
        self.thread_lock = threading.Lock()
        self.last_request_end = 0.0
        self.request_counter = 0
        # Key: (clean_path_lower) -> (expires_at_monotonic, parsed_json_dict)
        self.cache: dict[str, tuple[float, dict]] = {}

    def _extract_client_tx_id(self, query: str) -> int:
        params = parse_qs(query)
        for key, vals in params.items():
            if key.lower() == "clienttransactionid" and vals:
                try:
                    return int(vals[0])
                except ValueError:
                    return 0
        return 0

    def _try_cache(self, method: str, path: str) -> tuple[int, dict, bytes] | None:
        if method != "GET":
            return None

        parsed = urlsplit(path)
        clean_path = parsed.path.rstrip("/").lower()
        endpoint = clean_path.rsplit("/", 1)[-1] if "/" in clean_path else ""

        if endpoint not in STATIC_ENDPOINTS and endpoint not in TTL_ENDPOINTS:
            return None

        entry = self.cache.get(clean_path)
        if not entry:
            return None

        expires_at, cached_payload = entry
        if time.monotonic() > expires_at:
            del self.cache[clean_path]
            return None

        tx_id = self._extract_client_tx_id(parsed.query)
        reply = dict(cached_payload)
        reply["ClientTransactionID"] = tx_id
        body = json.dumps(reply, separators=(",", ":")).encode("utf-8")
        return 200, {"Content-Type": "application/json"}, body

    def _update_cache(self, method: str, path: str, status_code: int, resp_bytes: bytes) -> None:
        parsed = urlsplit(path)
        clean_path = parsed.path.rstrip("/").lower()
        endpoint = clean_path.rsplit("/", 1)[-1] if "/" in clean_path else ""

        # Any state-mutating PUT/POST invalidates matching GET cache or full cache on connect/exposure
        if method in ("PUT", "POST"):
            if endpoint in ("connected", "startexposure", "abortexposure", "stopexposure"):
                self.cache.clear()
            else:
                self.cache.pop(clean_path, None)
            return

        if method != "GET" or status_code != 200:
            return

        if endpoint not in STATIC_ENDPOINTS and endpoint not in TTL_ENDPOINTS:
            return

        try:
            payload = json.loads(resp_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return

        if payload.get("ErrorNumber", 0) != 0:
            return

        ttl = float("inf") if endpoint in STATIC_ENDPOINTS else self.cache_ttl_s
        self.cache[clean_path] = (time.monotonic() + ttl, payload)

    def forward(self, method: str, path: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]:
        with self.thread_lock:
            cached = self._try_cache(method, path)
            if cached is not None:
                return cached

            self.request_counter += 1
            req_id = self.request_counter

            # Enforce cooldown since the last upstream request finished
            now = time.monotonic()
            elapsed = now - self.last_request_end
            if self.last_request_end > 0 and elapsed < self.cooldown_s:
                time.sleep(self.cooldown_s - elapsed)

            # Filter hop-by-hop headers
            fwd_headers = {
                k: v for k, v in headers.items()
                if k.lower() not in ("host", "connection", "content-length", "transfer-encoding")
            }

            body_preview = body.decode("utf-8", errors="replace").strip() if body else ""
            logger.info(f"#{req_id} -> {method} {path} {f'| Body: {body_preview}' if body_preview else ''}")

            start_t = time.monotonic()
            try:
                resp = self.client.request(
                    method=method,
                    url=path,
                    headers=fwd_headers,
                    content=body,
                )
                duration_ms = (time.monotonic() - start_t) * 1000.0
                self.last_request_end = time.monotonic()

                resp_preview = resp.text[:200].replace("\n", " ")
                logger.info(f"#{req_id} <- {resp.status_code} ({duration_ms:.1f}ms) | {resp_preview}")

                self._update_cache(method, path, resp.status_code, resp.content)

                resp_headers = {
                    k: v for k, v in resp.headers.items()
                    if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
                }
                return resp.status_code, resp_headers, resp.content

            except httpx.RequestError as exc:
                duration_ms = (time.monotonic() - start_t) * 1000.0
                self.last_request_end = time.monotonic()
                logger.error(f"#{req_id} !! SEESTAR CONNECTION FAILED ({duration_ms:.1f}ms) on {method} {path}: {exc}")
                error_payload = f'{{"ErrorNumber": 1024, "ErrorMessage": "Proxy upstream failure: {exc}"}}'.encode()
                return 502, {"Content-Type": "application/json"}, error_payload


def make_handler(proxy: SerializedAlpacaProxy):
    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle()

        def do_PUT(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def _handle(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else None

            status, resp_headers, resp_body = proxy.forward(
                method=self.command,
                path=self.path,
                headers=dict(self.headers),
                body=body,
            )

            self.send_response(status)
            for k, v in resp_headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)

        def log_message(self, format, *args):
            pass

    return ProxyHandler


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Serializing ASCOM Alpaca Proxy for ZWO Seestar",
    )
    parser.add_argument("--seestar-ip", default="10.0.0.1", help="IP address of the Seestar")
    parser.add_argument("--seestar-port", type=int, default=32323, help="Seestar Alpaca port")
    parser.add_argument("--listen-host", default="0.0.0.0", help="Local bind IP")
    parser.add_argument("--listen-port", type=int, default=32323, help="Local listen port for N.I.N.A.")
    parser.add_argument("--cooldown-ms", type=int, default=300, help="Minimum delay in ms between upstream requests")
    parser.add_argument("--cache-ttl-ms", type=int, default=2000, help="TTL in ms for cached status GET responses")
    parser.add_argument("--timeout", type=float, default=10.0, help="Upstream HTTP timeout in seconds")
    args = parser.parse_args()

    proxy = SerializedAlpacaProxy(
        target_host=args.seestar_ip,
        target_port=args.seestar_port,
        cooldown_ms=args.cooldown_ms,
        timeout_s=args.timeout,
        cache_ttl_ms=args.cache_ttl_ms,
    )

    server = ThreadingHTTPServer((args.listen_host, args.listen_port), make_handler(proxy))
    logger.info(
        f"Listening on http://{args.listen_host}:{args.listen_port} -> "
        f"http://{args.seestar_ip}:{args.seestar_port} "
        f"(cooldown: {args.cooldown_ms}ms, cache TTL: {args.cache_ttl_ms}ms)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down proxy.")
        server.server_close()


if __name__ == "__main__":
    main()



"""Local reverse proxy in front of the vLLM OpenAI-compatible API.

The upstream runner (``unimodal/langgraph_runner_unimodal.py``) posts to
``{endpoint}/v1/chat/completions`` with **no ``Authorization`` header and no
``model`` field**, so it cannot talk to a vLLM server started with
``--api-key``. This proxy listens on localhost, forwards every request to
the vLLM server, and injects:

* ``Authorization: Bearer <key>`` — key read from the environment variable
  named by ``--api-key-env`` (default ``VLLM_API_KEY``);
* ``"model"`` (default ``qwen3.8-27b``, must match the server's
  ``--served-model-name``) when the request body carries none;
* ``chat_template_kwargs.enable_thinking = false`` so the model does not
  spend its token budget on hidden reasoning (same setting the owner's
  other repo uses for its vLLM agents).

Stdlib only; the key and request bodies are never logged.

Example:
    python comparison/vllm_proxy.py --listen 127.0.0.1:18001
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

DEFAULT_LISTEN = "127.0.0.1:18001"
DEFAULT_UPSTREAM = "http://100.73.85.24:1235"
DEFAULT_MODEL = "qwen3.8-27b"
UPSTREAM_TIMEOUT = 6000


def parse_args(argv=None):
    """Parse CLI arguments.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).
    Returns:
        Parsed argparse namespace.
    """
    parser = argparse.ArgumentParser(
        description="Local reverse proxy that adds auth and vLLM request fields."
    )
    parser.add_argument(
        "--listen",
        default=DEFAULT_LISTEN,
        help="Local host:port to listen on (default %(default)s)",
    )
    parser.add_argument(
        "--upstream",
        default=DEFAULT_UPSTREAM,
        help="vLLM base URL without a trailing /v1 (default %(default)s)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model name to inject when the body has none (default %(default)s)",
    )
    parser.add_argument(
        "--api-key-env",
        default="VLLM_API_KEY",
        help="Name of the env var holding the bearer key (default %(default)s)",
    )
    parser.add_argument(
        "--allow-thinking",
        action="store_true",
        help="Do not inject chat_template_kwargs.enable_thinking=false",
    )
    return parser.parse_args(argv)


class ProxyHandler(BaseHTTPRequestHandler):
    """Forwards requests to the upstream vLLM server and relays the reply.

    The class attribute ``proxy_config`` (a SimpleNamespace with
    ``upstream``, ``model``, ``api_key``, ``allow_thinking``) is set in
    ``main()`` before the server starts.
    """

    protocol_version = "HTTP/1.1"
    proxy_config = None

    def _rewrite_body(self, body):
        """Add the model name and thinking-off kwargs to a JSON body.

        Args:
            body: Raw request body bytes (or None for a bodyless request).
        Returns:
            The (possibly rewritten) body bytes; the original body is kept
            when it is not a JSON object.
        """
        if not body:
            return body
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return body
        if not isinstance(payload, dict):
            return body
        payload.setdefault("model", self.proxy_config.model)
        if not self.proxy_config.allow_thinking:
            kwargs = payload.get("chat_template_kwargs")
            if not isinstance(kwargs, dict):
                kwargs = {}
            kwargs["enable_thinking"] = False
            payload["chat_template_kwargs"] = kwargs
        return json.dumps(payload).encode("utf-8")

    def _forward(self, method, body=None):
        """Forward one request upstream and write the response to the client.

        Args:
            method: HTTP method to forward (GET/POST).
            body: Raw request body bytes to send upstream (POST only).
        Returns:
            None. Status line, Content-Type and the full body are written
            to the client connection.
        """
        cfg = self.proxy_config
        body = self._rewrite_body(body) if method == "POST" else body
        request = urllib.request.Request(
            cfg.upstream + self.path,
            data=body,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.api_key}",
            },
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT) as resp:
                status = resp.status
                payload = resp.read()
                content_type = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
            content_type = e.headers.get("Content-Type", "application/json")
        except Exception as e:
            status = 502
            content_type = "application/json"
            payload = json.dumps({"error": f"upstream unreachable: {e}"}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        print(
            f"{method} {self.path} -> {status} ({time.time() - started:.1f}s)",
            flush=True,
        )

    def do_GET(self):
        """Forward a GET request (e.g. ``/v1/models``)."""
        self._forward("GET")

    def do_POST(self):
        """Forward a POST request, rewriting the chat-completions body."""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        self._forward("POST", body)

    def log_message(self, fmt, *args):
        """Suppress the default stderr access log (one summary line is enough)."""


def main(argv=None):
    """Start the proxy until interrupted.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).
    Returns:
        Process exit code (0 on normal shutdown, 2 when the key is missing).
    """
    args = parse_args(argv)
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        print(
            f"error: environment variable {args.api_key_env} is not set; "
            "copy comparison/.env.example to comparison/.env (or export it) and retry",
            file=sys.stderr,
        )
        return 2
    cfg = SimpleNamespace(
        upstream=args.upstream.rstrip("/"),
        model=args.model,
        api_key=api_key,
        allow_thinking=args.allow_thinking,
    )
    ProxyHandler.proxy_config = cfg
    host, _, port = args.listen.partition(":")
    server = ThreadingHTTPServer((host, int(port or 0)), ProxyHandler)
    print(
        f"vllm_proxy: {args.listen} -> {cfg.upstream} "
        f"(model={cfg.model}, thinking_off={not cfg.allow_thinking})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

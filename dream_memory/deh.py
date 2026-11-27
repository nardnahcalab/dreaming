"""
DEH client: Dell Enterprise Hub endpoint discovery + OpenAI-compatible chat.

Replaces the old Ollama `LocalLLM` (localhost:11434, /api/chat).

Design (see deh_migration.html):
  - Discovery ladder: explicit env var -> dell_ai SDK deployment registry
    -> direct `docker ps` scan -> verify with GET /v1/models.
  - Wire protocol: OpenAI-compatible /v1/chat/completions (SGLang serves this).
  - `dell-ai` SDK is OPTIONAL. If it is not importable we degrade to the
    docker scan; if no endpoint is found at all, `available()` returns False
    and the dreamer falls back to the heuristic consolidator.

The HTTP layer stays stdlib-only (urllib) so the pipeline keeps working
without the SDK installed.
"""

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# DEH images are all prefixed with this.
DEH_IMAGE_PREFIX = "registry.dell.huggingface.co/enterprise-dell-inference-"

# Container port patterns: 0.0.0.0:8080->3000/tcp, 127.0.0.1:8080->3000/tcp,
# [::]:8080->3000/tcp. DEH's own parser only matches 0.0.0.0, which misses
# loopback-bound containers (observed on this box with laya-deh).
_PORT_RE = re.compile(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]|::):(\d+)->")


def _slug_from_image(image: str) -> str:
    """registry.../enterprise-dell-inference-<slug>[:tag] -> <slug>"""
    if not image.startswith(DEH_IMAGE_PREFIX):
        return ""
    return image[len(DEH_IMAGE_PREFIX):].split(":")[0]


def _endpoint_from_ports(ports_str: str) -> Optional[str]:
    m = _PORT_RE.search(ports_str or "")
    if not m:
        return None
    return "http://localhost:%s" % m.group(1)


def _docker_scan() -> List[Dict[str, Any]]:
    """Rung 3: find running DEH containers without needing the SDK."""
    if not shutil.which("docker"):
        return []
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}}|{{.Image}}|{{.Ports}}"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        cid, image, ports = parts
        if not image.startswith(DEH_IMAGE_PREFIX):
            continue
        out.append({
            "container_id": cid,
            "image": image,
            "slug": _slug_from_image(image),
            "endpoint": _endpoint_from_ports(ports),
        })
    return out


def _sdk_deployments() -> List[Dict[str, Any]]:
    """Rung 2: use the dell-ai SDK registry (merges JSON file + docker)."""
    try:
        from dell_ai import deployments as _dep  # type: ignore
    except Exception:
        return []
    try:
        raw = _dep.list_deployments()
    except Exception:
        return []
    out = []
    for dep_id, meta in (raw or {}).items():
        image = str(meta.get("image") or "")
        endpoint = meta.get("endpoint")
        # The SDK's own parser misses 127.0.0.1 binds; recover it ourselves.
        if not endpoint and image:
            try:
                proc = subprocess.run(
                    ["docker", "port", str(meta.get("container_id") or "")],
                    capture_output=True, text=True, timeout=6,
                )
                if proc.returncode == 0:
                    endpoint = _endpoint_from_ports(proc.stdout.replace("\n", " "))
            except Exception:
                endpoint = None
        out.append({
            "deployment_id": dep_id,
            "image": image,
            "slug": _slug_from_image(image),
            "endpoint": endpoint,
        })
    return out


def _probe(endpoint: str, timeout: int = 5) -> Optional[List[str]]:
    """Rung 4a: verify an endpoint answers /v1/models."""
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/v1/models",
                                    timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
    return ids or None


def _probe_chat(endpoint: str, timeout: int = 8) -> bool:
    """
    Rung 4b: verify the endpoint actually serves /v1/chat/completions.

    App containers (laya-deh) answer /v1/models but 404 on chat, so
    /v1/models alone is not sufficient. A 404 means the route is absent;
    any other outcome (200, or 4xx/5xx from a live route) means it exists.
    """
    body = json.dumps({
        "model": "x",
        "messages": [{"role": "user", "content": "1"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as ex:
        return ex.code != 404          # route exists but rejected the body
    except Exception:
        return False                   # no route / connection refused


def discover_endpoint(model_hint: Optional[str] = None,
                      slug_hint: Optional[str] = None) -> Dict[str, Any]:
    """
    Walk the discovery ladder. Returns {"endpoint", "model", "source"} or
    {"endpoint": None, ...} when nothing usable is running.
    """
    slug_hint = (slug_hint or os.environ.get("DREAM_DEH_SLUG") or "").lower()

    # Rung 1: explicit override wins outright.
    explicit = os.environ.get("DREAM_DEH_ENDPOINT", "").strip()
    if explicit:
        ids = _probe(explicit)
        if ids:
            return {"endpoint": explicit.rstrip("/"),
                    "model": _pick_model(ids, model_hint), "source": "env"}
        # Explicit but not answering: still honour it (may be starting up).
        return {"endpoint": explicit.rstrip("/"),
                "model": model_hint, "source": "env(unverified)"}

    candidates: List[Dict[str, Any]] = []
    candidates.extend(_sdk_deployments())
    seen = {c.get("endpoint") for c in candidates if c.get("endpoint")}
    for c in _docker_scan():
        if c.get("endpoint") in seen:
            continue
        candidates.append(c)

    # Prefer the entry whose image slug matches the configured model, because
    # app containers (e.g. laya-deh) share the DEH prefix but serve no chat.
    def _rank(c: Dict[str, Any]) -> int:
        slug = (c.get("slug") or "").lower()
        if slug_hint and slug_hint in slug:
            return 0
        if model_hint:
            hint = model_hint.split("/")[-1].lower().replace("_", "-")
            if hint and hint in slug:
                return 1
        return 2

    for cand in sorted([c for c in candidates if c.get("endpoint")],
                       key=_rank):
        ids = _probe(cand["endpoint"])
        if not ids:
            continue
        # Reject app containers: they serve /v1/models but not chat.
        if not _probe_chat(cand["endpoint"]):
            continue
        return {"endpoint": cand["endpoint"],
                "model": _pick_model(ids, model_hint),
                "source": "sdk" if cand.get("deployment_id") else "docker"}

    return {"endpoint": None, "model": model_hint, "source": "none"}


def _pick_model(ids: List[str], hint: Optional[str]) -> str:
    """Prefer the configured id when the server agrees, else data[0]."""
    if hint:
        for i in ids:
            if i == hint or i.endswith("/" + hint.split("/")[-1]):
                return i
    return ids[0]


class DEHLLM:
    """OpenAI-compatible chat client for a Dell Enterprise Hub deployment."""

    def __init__(self, model: Optional[str] = None, endpoint: Optional[str] = None,
                 timeout: int = 900):
        self.timeout = timeout
        self._explicit_endpoint = endpoint or os.environ.get("DREAM_DEH_ENDPOINT")
        self._discovery: Optional[Dict[str, Any]] = None
        self.model = model or os.environ.get(
            "DREAM_MODEL", "meta-models/Muse-Glimmer-30B")
        # Model actually served, once known.
        self.served_model: Optional[str] = None

    # -- discovery -------------------------------------------------------
    def _discover(self) -> Dict[str, Any]:
        if self._discovery is None:
            self._discovery = discover_endpoint(
                model_hint=self.model,
                slug_hint=os.environ.get("DREAM_DEH_SLUG"),
            )
            if self._discovery.get("model"):
                self.served_model = self._discovery["model"]
        return self._discovery

    @property
    def endpoint(self) -> Optional[str]:
        return self._discover().get("endpoint")

    def invalidate(self) -> None:
        """Forget cached discovery (useful after deploy/undeploy)."""
        self._discovery = None
        self.served_model = None

    # -- API -------------------------------------------------------------
    def available(self) -> bool:
        ep = self.endpoint
        if not ep:
            return False
        if _probe(ep, timeout=5):
            return True
        # Model may still be loading: /health answers before weights are ready.
        try:
            with urllib.request.urlopen(ep + "/health", timeout=5) as r:
                return r.status == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        ep = self.endpoint
        if not ep:
            return []
        return _probe(ep, timeout=10) or []

    def endpoint_info(self) -> Dict[str, Any]:
        """For `dream.py status` / `dream.py models`."""
        d = self._discover()
        return {
            "endpoint": d.get("endpoint"),
            "source": d.get("source"),
            "configured_model": self.model,
            "served_model": self.served_model,
            "sdk_installed": _sdk_available(),
        }

    def chat(self, system: str, prompt: str, temperature: float = 0.2) -> str:
        ep = self.endpoint
        if not ep:
            raise RuntimeError("no DEH endpoint discovered")

        model = self.served_model or self.model
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

        def _post(body: Dict[str, Any]) -> str:
            req = urllib.request.Request(
                ep + "/v1/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read())
            return resp["choices"][0]["message"].get("content") or ""

        body = {
            "model": model,
            "stream": False,
            "temperature": temperature,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        try:
            return _post(body)
        except urllib.error.HTTPError as ex:
            # Older OpenAI-compatible servers may not accept response_format.
            # Retry once without it; _extract_json() still sanitises the reply.
            if ex.code in (400, 404, 422):
                body.pop("response_format", None)
                return _post(body)
            raise


def _sdk_available() -> bool:
    try:
        import dell_ai  # noqa: F401
        return True
    except Exception:
        return False

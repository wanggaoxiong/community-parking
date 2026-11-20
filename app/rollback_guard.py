"""Autonomous health monitor & zero-loop auto-rollback guard for Docker deployments.

Watches the `community-parking` container over `/var/run/docker.sock`:
1. When `community-parking` is running + `healthy`, pins its image as
   `community-parking:last-known-good` (preserving the prior one as
   `community-parking:previous-good`), snapshots its exact container creation spec,
   and prunes dangling layers so disk usage stays bounded.
2. If Watchtower updates `community-parking` to a broken release (`unhealthy`,
   crash-exited, or stuck starting past the grace window), immediately recreates
   `community-parking` from `community-parking:last-known-good` with label
   `com.centurylinklabs.watchtower.enable=false` and records `blocked_bad_image_id`
   so Watchtower never enters an infinite re-update loop.
3. While in rolled-back protection mode, probes the upstream registry image every
   few minutes; as soon as a newer fixed digest (`!= blocked_bad_image_id`) is
   published, automatically upgrades `community-parking` and re-enables Watchtower.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time
from typing import Any
import urllib.parse


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_image_reference(image_ref: str) -> tuple[str, str]:
    """Split an image reference into `(fromImage, tag)` for Docker's `/images/create`."""
    ref = image_ref.strip()
    if "@" in ref:
        repo, digest = ref.split("@", 1)
        return repo, digest
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    if last_colon > last_slash:
        return ref[:last_colon], ref[last_colon + 1 :]
    return ref, "latest"


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP/1.1 connection speaking over a Unix domain socket (`/var/run/docker.sock`)."""

    def __init__(self, sock_path: str, timeout: float = 30.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._sock_path = sock_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._sock_path)
        self.sock = sock


class DockerAPIError(RuntimeError):
    def __init__(self, status: int, message: str, method: str = "", path: str = "") -> None:
        super().__init__(f"Docker API {method} {path} -> HTTP {status}: {message}")
        self.status = status
        self.message = message


class DockerUnixClient:
    """Minimal zero-dependency Docker Engine client with automatic API version negotiation."""

    def __init__(self, sock_path: str = "/var/run/docker.sock", timeout: float = 30.0) -> None:
        self.sock_path = sock_path
        self.timeout = timeout
        self._api_prefix: str | None = None

    def _raw_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, bytes]:
        conn = _UnixHTTPConnection(self.sock_path, timeout=timeout or self.timeout)
        try:
            headers = {"Host": "localhost", "Accept": "application/json"}
            payload_bytes: bytes | None = None
            if body is not None:
                payload_bytes = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(payload_bytes))
            conn.request(method, path, body=payload_bytes, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, raw
        finally:
            conn.close()

    def api_prefix(self) -> str:
        if self._api_prefix is not None:
            return self._api_prefix
        status, raw = self._raw_request("GET", "/version", timeout=10.0)
        if status == 200 and raw:
            info = json.loads(raw.decode("utf-8", errors="replace"))
            api_ver = str(info.get("ApiVersion") or "").strip()
            if api_ver:
                self._api_prefix = f"/v{api_ver}"
                return self._api_prefix
        self._api_prefix = ""
        return self._api_prefix

    def request_json(
        self,
        method: str,
        subpath: str,
        body: dict[str, Any] | None = None,
        timeout: float | None = None,
        allow_404: bool = False,
    ) -> Any:
        full_path = f"{self.api_prefix()}{subpath}"
        status, raw = self._raw_request(method, full_path, body=body, timeout=timeout)
        if allow_404 and status == 404:
            return None
        if status >= 400:
            msg = raw.decode("utf-8", errors="replace").strip()
            raise DockerAPIError(status, msg, method=method, path=full_path)
        if not raw:
            return {}
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    def inspect_container(self, name_or_id: str) -> dict[str, Any] | None:
        quoted = urllib.parse.quote(name_or_id, safe="")
        return self.request_json("GET", f"/containers/{quoted}/json", allow_404=True)

    def inspect_image(self, image_ref: str) -> dict[str, Any] | None:
        quoted = urllib.parse.quote(image_ref, safe="")
        return self.request_json("GET", f"/images/{quoted}/json", allow_404=True)

    def tag_image(self, source_image: str, repo: str, tag: str) -> None:
        quoted_src = urllib.parse.quote(source_image, safe="")
        qs = urllib.parse.urlencode({"repo": repo, "tag": tag})
        self.request_json("POST", f"/images/{quoted_src}/tag?{qs}")

    def pull_image(self, image_ref: str) -> None:
        from_image, tag = parse_image_reference(image_ref)
        qs = urllib.parse.urlencode({"fromImage": from_image, "tag": tag})
        full_path = f"{self.api_prefix()}/images/create?{qs}"
        status, raw = self._raw_request("POST", full_path, timeout=180.0)
        if status >= 400:
            raise DockerAPIError(
                status,
                raw.decode("utf-8", errors="replace").strip(),
                method="POST",
                path=full_path,
            )

    def prune_dangling_images(self) -> None:
        filters = json.dumps({"dangling": ["true"]})
        qs = urllib.parse.urlencode({"filters": filters})
        self.request_json("POST", f"/images/prune?{qs}", timeout=60.0)

    def stop_container(self, name_or_id: str, stop_timeout: int = 20) -> None:
        quoted = urllib.parse.quote(name_or_id, safe="")
        try:
            self.request_json(
                "POST",
                f"/containers/{quoted}/stop?t={int(stop_timeout)}",
                timeout=max(30.0, float(stop_timeout) + 10.0),
                allow_404=True,
            )
        except DockerAPIError as exc:
            if exc.status != 304:  # 304 = container already stopped
                raise

    def remove_container(self, name_or_id: str, force: bool = True) -> None:
        quoted = urllib.parse.quote(name_or_id, safe="")
        qs = urllib.parse.urlencode({"force": "true" if force else "false", "v": "false"})
        self.request_json("DELETE", f"/containers/{quoted}?{qs}", allow_404=True)

    def create_container(self, name: str, create_payload: dict[str, Any]) -> str:
        qs = urllib.parse.urlencode({"name": name})
        res = self.request_json("POST", f"/containers/create?{qs}", body=create_payload)
        return str(res.get("Id") or "")

    def connect_network(self, network_name: str, container_id: str, aliases: list[str]) -> None:
        quoted_net = urllib.parse.quote(network_name, safe="")
        body = {
            "Container": container_id,
            "EndpointConfig": {"Aliases": aliases},
        }
        self.request_json("POST", f"/networks/{quoted_net}/connect", body=body)

    def start_container(self, name_or_id: str) -> None:
        quoted = urllib.parse.quote(name_or_id, safe="")
        self.request_json("POST", f"/containers/{quoted}/start")


def build_recreate_spec(inspect_data: dict[str, Any], default_name: str = "community-parking") -> dict[str, Any]:
    """Extract a clean, reusable ContainerCreate spec + secondary networks from `docker inspect`."""
    raw_id = str(inspect_data.get("Id") or "")
    short_id = raw_id[:12] if raw_id else ""
    raw_name = str(inspect_data.get("Name") or "").lstrip("/") or default_name

    cfg = inspect_data.get("Config") or {}
    host_cfg = inspect_data.get("HostConfig") or {}
    net_settings = inspect_data.get("NetworkSettings") or {}
    networks = net_settings.get("Networks") or {}

    allowed_cfg_keys = (
        "Image",
        "Env",
        "Cmd",
        "Entrypoint",
        "WorkingDir",
        "ExposedPorts",
        "Labels",
        "Healthcheck",
        "StopSignal",
        "StopTimeout",
        "User",
        "Volumes",
    )
    create_body: dict[str, Any] = {}
    for key in allowed_cfg_keys:
        if key in cfg and cfg[key] is not None:
            val = cfg[key]
            create_body[key] = dict(val) if isinstance(val, dict) else (list(val) if isinstance(val, list) else val)

    allowed_host_keys = (
        "Binds",
        "PortBindings",
        "RestartPolicy",
        "LogConfig",
        "ExtraHosts",
        "NetworkMode",
        "ShmSize",
        "Tmpfs",
        "CapAdd",
        "CapDrop",
        "SecurityOpt",
    )
    clean_host_cfg: dict[str, Any] = {}
    for key in allowed_host_keys:
        if key in host_cfg and host_cfg[key] is not None:
            clean_host_cfg[key] = host_cfg[key]
    create_body["HostConfig"] = clean_host_cfg

    primary_net = str(clean_host_cfg.get("NetworkMode") or "")
    if (not primary_net or primary_net == "default") and networks:
        primary_net = next(iter(networks.keys()))

    extra_networks: dict[str, list[str]] = {}
    builtin_modes = {"bridge", "host", "none", "default", ""}
    if networks:
        for net_name, net_obj in networks.items():
            raw_aliases = list((net_obj or {}).get("Aliases") or [])
            clean_aliases: list[str] = []
            for alias in raw_aliases:
                if alias and alias != short_id and alias not in clean_aliases:
                    clean_aliases.append(alias)
            if raw_name not in clean_aliases:
                clean_aliases.append(raw_name)
            if net_name == primary_net and net_name not in builtin_modes:
                create_body["NetworkingConfig"] = {
                    "EndpointsConfig": {net_name: {"Aliases": clean_aliases}}
                }
            elif net_name not in builtin_modes:
                extra_networks[net_name] = clean_aliases

    return {
        "container_name": raw_name,
        "create_payload": create_body,
        "extra_networks": extra_networks,
    }


class RollbackGuard:
    """Stateful health monitor and automatic rollback controller."""

    WATCHTOWER_LABEL = "com.centurylinklabs.watchtower.enable"
    GOOD_REPO = "community-parking"
    LAST_GOOD_TAG = "last-known-good"
    PREV_GOOD_TAG = "previous-good"

    def __init__(
        self,
        docker_client: DockerUnixClient,
        data_dir: str | Path = "/app/data",
        target_container: str = "community-parking",
        upstream_image: str = "ghcr.io/wanggaoxiong/community-parking:latest",
        startup_grace_seconds: float = 75.0,
        upstream_probe_seconds: float = 300.0,
    ) -> None:
        self.docker = docker_client
        self.target_container = target_container
        self.upstream_image = upstream_image
        self.startup_grace_seconds = startup_grace_seconds
        self.upstream_probe_seconds = upstream_probe_seconds

        self.autoupdate_dir = Path(data_dir) / "autoupdate"
        self.autoupdate_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.autoupdate_dir / "state.json"
        self.spec_path = self.autoupdate_dir / "last_good_spec.json"
        self.log_path = self.autoupdate_dir / "rollback.log"

        self._unhealthy_since: dict[str, float] = {}
        self._last_upstream_probe_ts: float = 0.0
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        default_state: dict[str, Any] = {
            "target_container": self.target_container,
            "upstream_image": self.upstream_image,
            "last_good_image_id": "",
            "previous_good_image_id": "",
            "blocked_bad_image_id": "",
            "rollback_active": False,
            "rollback_count": 0,
            "last_healthy_at": "",
            "last_rollback_at": "",
            "last_rollback_reason": "",
        }
        if self.state_path.exists():
            try:
                loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    default_state.update(loaded)
            except Exception:
                pass
        default_state["target_container"] = self.target_container
        default_state["upstream_image"] = self.upstream_image
        return default_state

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def _log(self, message: str) -> None:
        line = f"[{_utc_now_iso()}] {message}"
        print(line, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            # Bound log size (~200KB max)
            if self.log_path.stat().st_size > 200_000:
                lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
                self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

    def _recreate_target(
        self,
        image_ref: str,
        watchtower_enabled: bool,
        fallback_inspect: dict[str, Any] | None = None,
    ) -> str:
        spec_bundle: dict[str, Any] | None = None
        if self.spec_path.exists():
            try:
                spec_bundle = json.loads(self.spec_path.read_text(encoding="utf-8"))
            except Exception:
                spec_bundle = None
        if not spec_bundle and fallback_inspect:
            spec_bundle = build_recreate_spec(fallback_inspect, default_name=self.target_container)
        if not spec_bundle:
            raise RuntimeError("No saved last_good_spec.json or live container spec available for recreation")

        create_payload = json.loads(json.dumps(spec_bundle["create_payload"]))
        create_payload["Image"] = image_ref
        labels = dict(create_payload.get("Labels") or {})
        labels[self.WATCHTOWER_LABEL] = "true" if watchtower_enabled else "false"
        create_payload["Labels"] = labels

        self.docker.stop_container(self.target_container, stop_timeout=20)
        self.docker.remove_container(self.target_container, force=True)
        new_id = self.docker.create_container(self.target_container, create_payload)
        for net_name, aliases in (spec_bundle.get("extra_networks") or {}).items():
            self.docker.connect_network(net_name, new_id, list(aliases))
        self.docker.start_container(new_id or self.target_container)
        return new_id

    def _record_healthy_snapshot(self, inspect_data: dict[str, Any]) -> None:
        image_id = str(inspect_data.get("Image") or "").strip()
        if not image_id:
            return
        self._unhealthy_since.clear()
        self.state["last_healthy_at"] = _utc_now_iso()

        # Only promote a new baseline when NOT running in rolled-back safe mode, or when no baseline exists yet
        if image_id != self.state.get("last_good_image_id") and not self.state.get("rollback_active"):
            prev_good = str(self.state.get("last_good_image_id") or "")
            if prev_good and prev_good != image_id:
                try:
                    self.docker.tag_image(prev_good, self.GOOD_REPO, self.PREV_GOOD_TAG)
                    self.state["previous_good_image_id"] = prev_good
                except Exception as exc:
                    self._log(f"⚠️ Failed to tag previous-good ({prev_good[:19]}): {exc}")

            self.docker.tag_image(image_id, self.GOOD_REPO, self.LAST_GOOD_TAG)
            spec_bundle = build_recreate_spec(inspect_data, default_name=self.target_container)
            # Ensure saved baseline template defaults to upstream image + watchtower enabled
            spec_bundle["create_payload"]["Image"] = self.upstream_image
            base_labels = dict(spec_bundle["create_payload"].get("Labels") or {})
            base_labels[self.WATCHTOWER_LABEL] = "true"
            spec_bundle["create_payload"]["Labels"] = base_labels
            tmp_spec = self.spec_path.with_suffix(".tmp")
            tmp_spec.write_text(json.dumps(spec_bundle, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_spec.replace(self.spec_path)

            self.state["last_good_image_id"] = image_id
            try:
                self.docker.prune_dangling_images()
            except Exception:
                pass
            self._log(
                f"✅ Healthy release confirmed ({image_id[:19]}) -> pinned as "
                f"{self.GOOD_REPO}:{self.LAST_GOOD_TAG}"
            )
        self._save_state()

    def _maybe_probe_fixed_upstream(self, now_mono: float) -> dict[str, Any] | None:
        blocked_id = str(self.state.get("blocked_bad_image_id") or "").strip()
        if not self.state.get("rollback_active") or not blocked_id:
            return None
        if (now_mono - self._last_upstream_probe_ts) < self.upstream_probe_seconds:
            return None
        self._last_upstream_probe_ts = now_mono

        try:
            self.docker.pull_image(self.upstream_image)
            img_info = self.docker.inspect_image(self.upstream_image)
        except Exception as exc:
            self._log(f"ℹ️ Upstream probe ({self.upstream_image}) skipped/failed: {exc}")
            return None

        if not img_info:
            return None
        upstream_id = str(img_info.get("Id") or "").strip()
        if not upstream_id or upstream_id == blocked_id:
            return None

        self._log(
            f"🔄 Detected newer upstream image {upstream_id[:19]} (!= blocked {blocked_id[:19]}). "
            f"Automatically upgrading {self.target_container} and re-enabling Watchtower..."
        )
        new_cid = self._recreate_target(self.upstream_image, watchtower_enabled=True)
        self.state["rollback_active"] = False
        self.state["blocked_bad_image_id"] = ""
        self._save_state()
        return {"action": "resumed_autoupdate", "new_image_id": upstream_id, "container_id": new_cid}

    def trigger_rollback(
        self,
        reason: str,
        bad_image_id: str = "",
        override_target_image: str = "",
        pause_watchtower: bool = True,
        fallback_inspect: dict[str, Any] | None = None,
        now_mono: float | None = None,
    ) -> dict[str, Any]:
        rollback_ref = override_target_image.strip()
        if not rollback_ref:
            last_good_id = str(self.state.get("last_good_image_id") or "").strip()
            if not last_good_id:
                raise RuntimeError("Cannot rollback: no last-known-good image has been recorded yet")
            if self.docker.inspect_image(f"{self.GOOD_REPO}:{self.LAST_GOOD_TAG}"):
                rollback_ref = f"{self.GOOD_REPO}:{self.LAST_GOOD_TAG}"
            else:
                rollback_ref = last_good_id

        self._log(
            f"🚨 Rolling back {self.target_container} -> {rollback_ref} "
            f"(pause_watchtower={pause_watchtower}) | Reason: {reason}"
        )
        new_cid = self._recreate_target(
            rollback_ref,
            watchtower_enabled=(not pause_watchtower),
            fallback_inspect=fallback_inspect,
        )
        self.state["rollback_count"] = int(self.state.get("rollback_count") or 0) + 1
        self.state["last_rollback_at"] = _utc_now_iso()
        self.state["last_rollback_reason"] = reason
        self.state["rollback_active"] = bool(pause_watchtower)
        if bad_image_id and (
            override_target_image or bad_image_id != self.state.get("last_good_image_id")
        ):
            self.state["blocked_bad_image_id"] = bad_image_id
        if (
            override_target_image == f"{self.GOOD_REPO}:{self.PREV_GOOD_TAG}"
            and self.state.get("previous_good_image_id")
        ):
            prev_id = str(self.state["previous_good_image_id"])
            try:
                self.docker.tag_image(prev_id, self.GOOD_REPO, self.LAST_GOOD_TAG)
                self.state["last_good_image_id"] = prev_id
            except Exception:
                pass
        self._unhealthy_since.clear()
        self._last_upstream_probe_ts = time.monotonic() if now_mono is None else now_mono
        self._save_state()
        self._log(f"✅ Rollback completed -> container {new_cid[:12]} started from {rollback_ref}")
        return {
            "action": "rolled_back",
            "restored_image": rollback_ref,
            "blocked_bad_image_id": self.state.get("blocked_bad_image_id", ""),
            "container_id": new_cid,
            "reason": reason,
        }

    def step(self, now_mono: float | None = None) -> dict[str, Any]:
        """Execute a single health-evaluation tick."""
        now = time.monotonic() if now_mono is None else now_mono
        inspect_data = self.docker.inspect_container(self.target_container)

        if inspect_data is None:
            if self.state.get("last_good_image_id") and self.spec_path.exists():
                return self.trigger_rollback(
                    reason=f"Container {self.target_container} missing after update",
                    pause_watchtower=bool(self.state.get("rollback_active")),
                    now_mono=now,
                )
            return {"action": "waiting_for_container"}

        c_state = inspect_data.get("State") or {}
        status = str(c_state.get("Status") or "").lower()
        running = bool(c_state.get("Running"))
        restarting = bool(c_state.get("Restarting"))
        health_obj = c_state.get("Health") or {}
        health_status = str(health_obj.get("Status") or ("healthy" if running else "none")).lower()
        current_image_id = str(inspect_data.get("Image") or "").strip()

        if running and not restarting and health_status == "healthy":
            self._record_healthy_snapshot(inspect_data)
            resumed = self._maybe_probe_fixed_upstream(now)
            if resumed:
                return resumed
            return {"action": "healthy", "image_id": current_image_id}

        # Immediate failure states: Docker HEALTHCHECK already exhausted retries (`unhealthy`) or container died
        last_good_id = str(self.state.get("last_good_image_id") or "").strip()
        if not last_good_id:
            return {"action": "warming_up_initial", "status": status, "health": health_status}

        if health_status == "unhealthy":
            return self.trigger_rollback(
                reason=f"Container {self.target_container} reported HEALTHCHECK=unhealthy on {current_image_id[:19]}",
                bad_image_id=current_image_id,
                pause_watchtower=(current_image_id != last_good_id),
                fallback_inspect=inspect_data,
                now_mono=now,
            )

        if status in {"exited", "dead"}:
            exit_code = c_state.get("ExitCode")
            return self.trigger_rollback(
                reason=f"Container {self.target_container} {status} (exit_code={exit_code}) on {current_image_id[:19]}",
                bad_image_id=current_image_id,
                pause_watchtower=(current_image_id != last_good_id),
                fallback_inspect=inspect_data,
                now_mono=now,
            )

        # Transitional states (`starting` / `restarting` / `created`): enforce grace window
        track_key = current_image_id or "unknown"
        first_seen = self._unhealthy_since.setdefault(track_key, now)
        elapsed = now - first_seen
        if elapsed >= self.startup_grace_seconds:
            return self.trigger_rollback(
                reason=(
                    f"Container {self.target_container} stuck in status={status}/health={health_status} "
                    f"for {int(elapsed)}s (> {int(self.startup_grace_seconds)}s) on {current_image_id[:19]}"
                ),
                bad_image_id=current_image_id,
                pause_watchtower=(current_image_id != last_good_id),
                fallback_inspect=inspect_data,
                now_mono=now,
            )

        return {
            "action": "observing_transition",
            "status": status,
            "health": health_status,
            "elapsed_seconds": round(elapsed, 1),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Community Parking Auto-Rollback Guard")
    parser.add_argument(
        "--sock",
        default=os.environ.get("DOCKER_SOCK_PATH", "/var/run/docker.sock"),
        help="Path to Docker Unix socket",
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("PARKING_DATA_DIR", "/app/data"),
        help="Persistent data directory",
    )
    parser.add_argument(
        "--container",
        default=os.environ.get("ROLLBACK_TARGET_CONTAINER", "community-parking"),
        help="Target container name to guard",
    )
    parser.add_argument(
        "--upstream-image",
        default=os.environ.get("PARKING_IMAGE", "ghcr.io/wanggaoxiong/community-parking:latest"),
        help="Upstream registry image watched by Watchtower",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("ROLLBACK_CHECK_INTERVAL", "15")),
        help="Health check polling interval in seconds",
    )
    parser.add_argument(
        "--grace",
        type=float,
        default=float(os.environ.get("ROLLBACK_STARTUP_GRACE", "75")),
        help="Startup grace period in seconds before rolling back a stuck container",
    )
    parser.add_argument(
        "--probe-interval",
        type=float,
        default=float(os.environ.get("ROLLBACK_UPSTREAM_PROBE_INTERVAL", "300")),
        help="Seconds between upstream fix probes while rolled back",
    )
    parser.add_argument("--once", action="store_true", help="Run a single check tick and exit")
    parser.add_argument("--status", action="store_true", help="Print guard JSON state and exit")
    parser.add_argument(
        "--rollback-now",
        nargs="?",
        const="auto",
        default=None,
        help="Immediately roll back target container to last-known-good / previous-good (or specified image tag)",
    )
    args = parser.parse_args(argv)

    client = DockerUnixClient(sock_path=args.sock)
    guard = RollbackGuard(
        docker_client=client,
        data_dir=args.data_dir,
        target_container=args.container,
        upstream_image=args.upstream_image,
        startup_grace_seconds=args.grace,
        upstream_probe_seconds=args.probe_interval,
    )

    if args.status:
        live = client.inspect_container(args.container)
        live_summary = None
        if live:
            st = live.get("State") or {}
            cfg = live.get("Config") or {}
            live_summary = {
                "status": st.get("Status"),
                "health": (st.get("Health") or {}).get("Status"),
                "running_image_ref": cfg.get("Image"),
                "running_image_id": live.get("Image"),
                "watchtower_enabled": (cfg.get("Labels") or {}).get(RollbackGuard.WATCHTOWER_LABEL),
            }
        print(json.dumps({"guard_state": guard.state, "live_container": live_summary}, ensure_ascii=False, indent=2))
        return 0

    if args.rollback_now is not None:
        live = client.inspect_container(args.container)
        live_img_id = str((live or {}).get("Image") or "")
        target_img = args.rollback_now
        if target_img == "auto":
            if (
                live_img_id
                and live_img_id == guard.state.get("last_good_image_id")
                and guard.state.get("previous_good_image_id")
                and client.inspect_image(f"{RollbackGuard.GOOD_REPO}:{RollbackGuard.PREV_GOOD_TAG}")
            ):
                target_img = f"{RollbackGuard.GOOD_REPO}:{RollbackGuard.PREV_GOOD_TAG}"
            else:
                target_img = f"{RollbackGuard.GOOD_REPO}:{RollbackGuard.LAST_GOOD_TAG}"
        if target_img not in {
            f"{RollbackGuard.GOOD_REPO}:{RollbackGuard.LAST_GOOD_TAG}",
            f"{RollbackGuard.GOOD_REPO}:{RollbackGuard.PREV_GOOD_TAG}",
        } and not client.inspect_image(target_img):
            print(f"📥 Pulling requested rollback image: {target_img} ...", flush=True)
            client.pull_image(target_img)
        res = guard.trigger_rollback(
            reason=f"Manual operator rollback to {target_img}",
            bad_image_id=live_img_id,
            override_target_image=target_img,
            pause_watchtower=True,
            fallback_inspect=live,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.once:
        res = guard.step()
        print(json.dumps(res, ensure_ascii=False))
        return 0

    stop_requested = False

    def _on_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    guard._log(
        f"🛡️ RollbackGuard started: watching '{args.container}' "
        f"(upstream={args.upstream_image}, interval={args.interval}s, grace={args.grace}s)"
    )
    while not stop_requested:
        try:
            guard.step()
        except Exception as exc:
            guard._log(f"⚠️ Guard step error: {exc}")
        deadline = time.monotonic() + max(2.0, args.interval)
        while not stop_requested and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.1, deadline - time.monotonic())))

    guard._log("🛑 RollbackGuard shutting down cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

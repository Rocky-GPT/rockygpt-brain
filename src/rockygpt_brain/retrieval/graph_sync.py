"""One shared copy of the release graph, the same file on the laptop and in production.

The daily data run builds the active release's graph file and meaning index once
(scripts/build_graph.py, scripts/build_meaning_index.py), then puts them in a file store (an
S3-style bucket) with scripts/publish_graph.py. Every graph-only Brain downloads that exact copy
into its BRAIN_GRAPH_ONLY_DIR with this module: production's Brain at start and then every few
minutes (GraphFollower), the laptop only when asked (`python -m rockygpt_brain.retrieval.graph_sync
DIR`, run by the workspace's sync-prod). So both serve the same bytes, through the same reader.

Store layout, under BRAIN_GRAPH_STORE_PREFIX (default `brain-graph/`):

    release-<hash>.lbug, release-<hash>.meaning.json, release-<hash>.meaning.f32
    active.json   the pointer GraphOnlyFacts reads, plus each file's size and sha256 ("files"),
                  written last so a reader sees the old release or the whole new one

Locally the release files go in first, each checked against its sha256, and `active.json` is
renamed into place last. The previous release's files stay until the next install, because a
running Brain may still be reading them. Nothing here reads the campus database.

Requests are signed with AWS Signature Version 4, which S3, Cloudflare R2 and MinIO all accept.
"""

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

LOG = logging.getLogger(__name__)

POINTER = "active.json"
MARKER = "artifact.json"  # What the dev UI's sync panel reads: when the copy was shared.
DEFAULT_PREFIX = "brain-graph/"
FOLLOW_SECONDS = 300.0  # How often production's Brain checks the store for a newer release.
RETRY_SECONDS = 60.0  # After a failed check, the next one comes this soon.
CHUNK = 1024 * 1024
TIMEOUT = httpx.Timeout(60.0, connect=15.0)
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

Emit = Callable[..., None]


class SyncError(Exception):
    """Why the shared copy could not be read or written. Never holds a credential."""


def quiet(step: str, **_: object) -> None:
    """The default: no step events."""


def json_events(step: str, **fields: object) -> None:
    """One JSON line per step on stdout, flushed so a reader (the dev UI) sees it at once."""
    print(json.dumps({"step": step, **fields}), flush=True)


@dataclass(frozen=True)
class GraphStore:
    """An S3-style bucket holding the shared release graph."""

    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str = "auto"
    endpoint: str = ""  # Empty: AWS S3 itself. Otherwise path-style requests to this URL.
    prefix: str = DEFAULT_PREFIX

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "GraphStore | None":
        """The store from BRAIN_GRAPH_STORE_* settings, or None when no bucket is set. A bucket
        without both keys is a setup error, not a quiet "no store"."""
        env = os.environ if environ is None else environ
        bucket = env.get("BRAIN_GRAPH_STORE_BUCKET", "").strip()
        if not bucket:
            return None
        key_id = env.get("BRAIN_GRAPH_STORE_ACCESS_KEY_ID", "").strip()
        secret = env.get("BRAIN_GRAPH_STORE_SECRET_ACCESS_KEY", "").strip()
        if not key_id or not secret:
            raise SyncError("BRAIN_GRAPH_STORE_BUCKET is set but its access keys are not.")
        prefix = env.get("BRAIN_GRAPH_STORE_PREFIX", DEFAULT_PREFIX).strip()
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        return cls(bucket=bucket, access_key_id=key_id, secret_access_key=secret,
                   region=env.get("BRAIN_GRAPH_STORE_REGION", "").strip() or "auto",
                   endpoint=env.get("BRAIN_GRAPH_STORE_ENDPOINT", "").strip().rstrip("/"),
                   prefix=prefix)

    # --- requests -----------------------------------------------------------------------------

    def _url(self, name: str) -> tuple[str, str, str]:
        """(url, host, canonical path) for one object under the prefix."""
        key = quote(self.prefix + name, safe="/-_.~")
        if self.endpoint:
            base = httpx.URL(self.endpoint)
            path = f"{base.path.rstrip('/')}/{quote(self.bucket, safe='-_.~')}/{key}"
            host = base.host if base.port is None else f"{base.host}:{base.port}"
            return f"{base.scheme}://{host}{path}", host, path
        region = "us-east-1" if self.region == "auto" else self.region
        host = f"{self.bucket}.s3.{region}.amazonaws.com"
        return f"https://{host}/{key}", host, f"/{key}"

    def _headers(self, method: str, host: str, path: str, payload_sha256: str,
                 now: datetime | None = None) -> dict[str, str]:
        """AWS Signature Version 4 headers for a request with no query string."""
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        stamp, day = moment.strftime("%Y%m%dT%H%M%SZ"), moment.strftime("%Y%m%d")
        headers = {"host": host, "x-amz-content-sha256": payload_sha256, "x-amz-date": stamp}
        signed = ";".join(sorted(headers))
        canonical = "\n".join([method, path, "",
                               *(f"{k}:{headers[k]}" for k in sorted(headers)), "",
                               signed, payload_sha256])
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope,
                             hashlib.sha256(canonical.encode()).hexdigest()])
        key = f"AWS4{self.secret_access_key}".encode()
        for part in (day, self.region, "s3", "aws4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}")
        del headers["host"]  # httpx sends it from the URL; it was signed with the same value.
        return headers

    def get_json(self, name: str, client: httpx.Client) -> dict[str, Any] | None:
        """A small JSON object, or None when the store has no such object."""
        url, host, path = self._url(name)
        response = client.get(url, headers=self._headers("GET", host, path, EMPTY_SHA256))
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise SyncError(f"The file store answered {response.status_code} for {name}.")
        try:
            value = response.json()
        except ValueError as error:
            raise SyncError(f"{name} in the file store is not JSON.") from error
        if not isinstance(value, dict):
            raise SyncError(f"{name} in the file store is not a JSON object.")
        return value

    def download(self, name: str, target: Path, client: httpx.Client,
                 progress: Callable[[int], None] = lambda _: None) -> str:
        """Stream one object into target; returns its sha256."""
        url, host, path = self._url(name)
        digest = hashlib.sha256()
        with client.stream("GET", url,
                           headers=self._headers("GET", host, path, EMPTY_SHA256)) as response:
            if response.status_code != 200:
                raise SyncError(f"The file store answered {response.status_code} for {name}.")
            with target.open("wb") as out:
                for chunk in response.iter_bytes(CHUNK):
                    out.write(chunk)
                    digest.update(chunk)
                    progress(len(chunk))
        return digest.hexdigest()

    def put(self, name: str, body: bytes, client: httpx.Client,
            content_type: str = "application/octet-stream") -> None:
        url, host, path = self._url(name)
        headers = self._headers("PUT", host, path, hashlib.sha256(body).hexdigest())
        headers["content-type"] = content_type
        response = client.put(url, content=body, headers=headers)
        if response.status_code not in (200, 201, 204):
            raise SyncError(f"The file store answered {response.status_code} saving {name}.")

    def delete(self, name: str, client: httpx.Client) -> None:
        url, host, path = self._url(name)
        response = client.delete(url, headers=self._headers("DELETE", host, path, EMPTY_SHA256))
        if response.status_code not in (200, 204, 404):
            raise SyncError(f"The file store answered {response.status_code} removing {name}.")


# --- the pointer -------------------------------------------------------------------------------

def _flat(name: object) -> str:
    if not isinstance(name, str) or Path(name).name != name or not name.startswith("release-"):
        raise SyncError("The pointer names something other than a release file.")
    return name


def release_files(pointer: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The pointer's release files ({name, size, sha256}); the graph file must be one of them."""
    files = pointer.get("files")
    if not isinstance(files, list) or not files:
        raise SyncError("The shared active.json lists no files.")
    checked = []
    for entry in files:
        if (not isinstance(entry, dict) or not isinstance(entry.get("size"), int)
                or not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64):
            raise SyncError("The shared active.json lists a file without its size and sha256.")
        checked.append({"name": _flat(entry.get("name")), "size": entry["size"],
                        "sha256": entry["sha256"]})
    if _flat(pointer.get("file")) not in {entry["name"] for entry in checked}:
        raise SyncError("The shared active.json does not list its own graph file.")
    return checked


def _stem(pointer: Mapping[str, Any]) -> str:
    return Path(_flat(pointer.get("file"))).stem


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temp, path)


# --- downloading (every graph-only Brain) ------------------------------------------------------

def release_key(pointer: Mapping[str, Any]) -> str:
    """What makes two pointers the same release: its graph file, release, identities and files."""
    return json.dumps({k: pointer.get(k) for k in ("file", "dataset_version", "identity_hash",
                                                    "files")}, sort_keys=True)


def sync(store: GraphStore, directory: Path, *, force: bool = False, emit: Emit = quiet,
         client: httpx.Client | None = None) -> tuple[bool, str]:
    """Make directory serve the store's active release. Returns (changed, message).

    A file already here with the listed sha256 is not downloaded again; force re-checks and
    re-installs everything even when the release is the same."""
    remote, new = download(store, directory, force=force, emit=emit, client=client)
    if not new:
        return False, f"up to date (production data from {remote.get('published_at') or ''})"
    emit("installing")
    return True, install_release(directory, remote)


def download(store: GraphStore, directory: Path, *, force: bool = False, emit: Emit = quiet,
             client: httpx.Client | None = None, skip: str | None = None,
             ) -> tuple[dict[str, Any], bool]:
    """Download the store's active release into directory, checked, without serving it yet.
    Returns (its pointer, whether it still needs installing): False when directory already
    serves it, or when it is the release skip names (one already refused)."""
    directory.mkdir(parents=True, exist_ok=True)
    with ExitClient(client) as http:
        emit("checking")
        remote = store.get_json(POINTER, http)
        if remote is None:
            raise SyncError("The file store has no shared release graph yet.")
        files = release_files(remote)
        total = sum(entry["size"] for entry in files)
        shared = str(remote.get("published_at") or "")
        emit("found", shared=shared, total=total)
        if skip is not None and release_key(remote) == skip:
            return remote, False
        local = _read_json(directory / POINTER)
        same = local is not None and release_key(local) == release_key(remote)
        if same and not force and all(
                (directory / e["name"]).is_file()
                and (directory / e["name"]).stat().st_size == e["size"] for e in files):
            return remote, False
        received = 0

        def progress(count: int) -> None:
            nonlocal received
            received += count
            emit("downloading", received=received, total=total)

        emit("downloading", received=0, total=total)
        for entry in files:
            target = directory / entry["name"]
            if (target.is_file() and target.stat().st_size == entry["size"]
                    and _sha256(target) == entry["sha256"]):
                progress(entry["size"])
                continue
            part = directory / f".{entry['name']}.part"
            try:
                digest = store.download(entry["name"], part, http, progress)
                if digest != entry["sha256"] or part.stat().st_size != entry["size"]:
                    raise SyncError(f"{entry['name']} arrived damaged (its sha256 differs).")
                os.replace(part, target)
            finally:
                part.unlink(missing_ok=True)
        return remote, True


def install_release(directory: Path, remote: Mapping[str, Any]) -> str:
    """Serve a downloaded release from directory: write its pointer and marker, and remove the
    release files other than its own and the release it replaces."""
    shared = str(remote.get("published_at") or "")
    local = _read_json(directory / POINTER)
    previous = _stem(local) if local and local.get("file") else None
    _write_json(directory / POINTER, remote)
    _write_json(directory / MARKER, {"created_at": shared, "source": "file store",
                                     "dataset_version": remote.get("dataset_version")})
    keep = {_stem(remote), previous}
    for item in directory.iterdir():
        if item.name.startswith("release-") and not any(
                k and (item.name == k or item.name.startswith(k + ".")) for k in keep):
            item.unlink(missing_ok=True)
    return f"installed production release {remote.get('dataset_version')} (shared {shared})"


class ExitClient:
    """Use the caller's HTTP client, or open (and close) one for this call."""

    def __init__(self, client: httpx.Client | None) -> None:
        self.client, self.owned = client, client is None

    def __enter__(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(timeout=TIMEOUT, follow_redirects=False)
        return self.client

    def __exit__(self, *_: object) -> None:
        if self.owned and self.client is not None:
            self.client.close()


class GraphFollower:
    """Production's Brain: keep BRAIN_GRAPH_ONLY_DIR on the store's active release.

    fetch() downloads the store's release when it is newer than the one installed here, checked
    but not served yet; install() then serves it from this folder. The Brain installs a release
    only once it has read it in full in an engine of its own (api/releases.py checks every
    FOLLOW_SECONDS, RETRY_SECONDS after a failure), so a release that fails to open is never
    installed. sync_once() does both at once."""

    def __init__(self, store: GraphStore, directory: Path, *,
                 interval: float = FOLLOW_SECONDS, client: httpx.Client | None = None) -> None:
        self.store, self.directory, self.interval = store, Path(directory), interval
        self.client = client  # None: each check opens (and closes) its own.
        self.last_error: str | None = None
        self.last_message: str | None = None

    def wait(self) -> float:
        """Seconds until the next check: sooner after a failed one."""
        return self.interval if self.last_error is None else min(self.interval, RETRY_SECONDS)

    def _failed(self, error: Exception) -> None:
        self.last_error = type(error).__name__
        LOG.warning("brain_graph_sync_failed exception_type=%s reason=%s",
                    type(error).__name__, str(error)[:200] if isinstance(error, SyncError)
                    else "")

    def fetch(self, skip: str | None = None) -> dict[str, Any] | None:
        """The store's release, downloaded and checked, when it still needs installing here and
        is not the release skip names; None when nothing is new or the download failed."""
        try:
            remote, new = download(self.store, self.directory, skip=skip, client=self.client)
        except (SyncError, httpx.HTTPError, OSError) as error:
            self._failed(error)
            return None
        self.last_error = None
        return remote if new else None

    def install(self, pointer: Mapping[str, Any]) -> bool:
        """Serve a fetched release from this folder."""
        try:
            message = install_release(self.directory, pointer)
        except OSError as error:
            self._failed(error)
            return False
        self.last_message = message
        LOG.info("brain_graph_sync_installed %s", message)
        return True

    def sync_once(self) -> bool:
        try:
            changed, message = sync(self.store, self.directory, client=self.client)
        except (SyncError, httpx.HTTPError, OSError) as error:
            self._failed(error)
            return False
        self.last_error, self.last_message = None, message
        if changed:
            LOG.info("brain_graph_sync_installed %s", message)
        return changed


# --- uploading (the daily data run, scripts/publish_graph.py) ----------------------------------

def publish(store: GraphStore, directory: Path, *,
            client: httpx.Client | None = None) -> str:
    """Put directory's active release in the store, then its pointer. Returns a message.

    The store keeps this release and the one before it: a Brain mid-download of the old one
    still finds its files. Files the store already holds with the same sha256 are not sent."""
    local = _read_json(directory / POINTER)
    if local is None:
        raise SyncError(f"{directory / POINTER} is missing or unreadable.")
    stem = _stem(local)
    paths = sorted(p for p in directory.iterdir()
                   if p.is_file() and (p.name == stem or p.name.startswith(stem + ".")))
    if (directory / local["file"]) not in paths:
        raise SyncError(f"{local['file']} is not in {directory}.")
    files: list[dict[str, Any]] = [
        {"name": p.name, "size": p.stat().st_size, "sha256": _sha256(p)} for p in paths]
    with ExitClient(client) as http:
        remote = store.get_json(POINTER, http)
        held: dict[str, str] = {}
        if remote is not None:
            try:
                held = {e["name"]: e["sha256"] for e in release_files(remote)}
            except SyncError:
                held = {}
        for entry, path in zip(files, paths, strict=True):
            if held.get(entry["name"]) != entry["sha256"]:
                store.put(entry["name"], path.read_bytes(), http)
        names = {e["name"] for e in files}
        before = sorted(set(held) - names)
        older = [n for n in (remote or {}).get("previous", []) if isinstance(n, str)]
        pointer = dict(local) | {
            "files": files, "previous": before if before else older,
            "published_at": datetime.now(UTC).isoformat(),
        }
        store.put(POINTER, (json.dumps(pointer, indent=2) + "\n").encode(), http,
                  content_type="application/json")
        keep = names | set(pointer["previous"])
        for name in older:
            if name not in keep:
                store.delete(_flat(name), http)
    return (f"shared release {local.get('dataset_version')} "
            f"({sum(e['size'] for e in files) / 1e6:.1f} MB in {len(files)} files)")


# --- command line (the laptop's sync-prod) ------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download the shared release graph into DIR (what production serves).")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--force", action="store_true", help="re-check and re-install everything")
    parser.add_argument("--events", action="store_true", help="print each step as a JSON line")
    args = parser.parse_args(argv)
    emit = json_events if args.events else quiet
    try:
        store = GraphStore.from_env()
        if store is None:
            raise SyncError("BRAIN_GRAPH_STORE_BUCKET is not set.")
        _, message = sync(store, args.directory, force=args.force, emit=emit)
    except (SyncError, httpx.HTTPError, OSError) as error:
        text = str(error) if isinstance(error, SyncError) else (
            f"the download failed ({type(error).__name__})")
        emit("error", message=text)
        print(f"production data: {text}", file=sys.stderr)
        return 1
    pointer = _read_json(args.directory / POINTER) or {}
    emit("done", message=message, release=pointer.get("dataset_version"))
    if not args.events:
        print(f"production data: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

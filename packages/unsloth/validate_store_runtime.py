"""Validate the realized Unsloth app directly from its Nix store path.

This is intentionally a post-build host check.  It never activates or exports the
package, and it limits teardown to process groups proven to belong to the isolated
session created for the candidate app.
"""

import argparse
import hashlib
import http.client
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import IO

CANDIDATE_PORTS = tuple(range(8888, 8909))
HEALTH_HOST = "127.0.0.1"
HEALTH_PATH = "/api/health"
REQUIRED_HEALTH_FIELDS = {
    "service": "Unsloth UI Backend",
    "status": "healthy",
}
_STUDIO_ROOT_ID = re.compile(r"[0-9a-f]+", re.ASCII)
ISOLATION_VARIABLES = (
    "HOME",
    "CFFIXED_USER_HOME",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
)
_INHERITED_ENVIRONMENT_VARIABLES = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "USER",
    "__CF_USER_TEXT_ENCODING",
)
_FINDER_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_STORE_ROOT = Path("/nix/store")
_LSOF = Path("/usr/sbin/lsof")
_PS = Path("/bin/ps")
_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
_SANDBOX_LOOPBACK_HOST = "localhost"
_PROCESS_SNAPSHOT_FIELD_COUNT = 5
_RAW_PS_FIELD_COUNT = 4
_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300
_MAX_TCP_PORT = 65535
_FORCED_CLEANUP_TIMEOUT_SECONDS = 5
_SENTINEL_BIND_ATTEMPTS = 32

type ListenerIdentity = tuple[tuple[int, str, str], ...]


class ValidationError(RuntimeError):
    """The contained host-runtime contract was not satisfied."""


def _error_message(error: BaseException) -> str:
    """Render one error plus cleanup notes carried by its explicit cause chain."""
    details = [str(error)]
    current: BaseException | None = error
    while current is not None:
        details.extend(getattr(current, "__notes__", ()))
        current = current.__cause__
    return "; ".join(details)


@dataclass(frozen=True, slots=True)
class Listener:
    """One TCP listener reported by lsof."""

    pid: int
    command: str
    address: str
    port: int


@dataclass(frozen=True, slots=True)
class SentinelBaseline:
    """Exact identity of the validator-owned listener during candidate execution."""

    port: int
    identity: ListenerIdentity
    identity_sha256: str


@dataclass(frozen=True, slots=True)
class ListenerSentinel:
    """Live socket and its independently observed listener baseline."""

    listener: socket.socket
    baseline: SentinelBaseline


@dataclass(frozen=True, slots=True)
class Process:
    """The process identity needed for ancestry and containment checks."""

    pid: int
    ppid: int
    pgid: int
    sid: int
    command: str


@dataclass(frozen=True, slots=True)
class StoreEvidence:
    """Paths emitted by the embedded storePathAppCandidateSmoke check."""

    app_candidate: Path
    app_bundle: Path
    app_executable: Path
    backend_executable: Path
    backend_runtime_entrypoint: Path


@dataclass(frozen=True, slots=True)
class RuntimeEvidence:
    """Evidence captured before the isolated runtime is torn down."""

    app_pid: int
    backend_pid: int
    health: dict[str, str]
    listener_address: str
    owned_process_groups: tuple[int, ...]
    port: int
    session_id: int


def required_health_evidence(payload: object) -> dict[str, str] | None:
    """Return the source-required health fields while allowing documented extras."""
    if not isinstance(payload, Mapping):
        return None
    mapping = cast("Mapping[str, object]", payload)
    if not all(
        mapping.get(key) == value for key, value in REQUIRED_HEALTH_FIELDS.items()
    ):
        return None
    studio_root_id = mapping.get("studio_root_id")
    if (
        not isinstance(studio_root_id, str)
        or _STUDIO_ROOT_ID.fullmatch(studio_root_id) is None
    ):
        return None
    return {**REQUIRED_HEALTH_FIELDS, "studio_root_id": studio_root_id}


def direct_app_argv(app_bundle: Path) -> tuple[str, ...]:
    """Return the direct CFBundleExecutable invocation, without ``open``."""
    return (str(app_bundle / "Contents/MacOS/unsloth-studio"),)


def sandbox_profile(root: Path) -> str:
    """Build a macOS profile limited to the isolated root and candidate loopback."""
    root_literal = json.dumps(str(root.resolve()))
    local_filters = "\n".join(
        f'    (local ip "{_SANDBOX_LOOPBACK_HOST}:{port}")' for port in CANDIDATE_PORTS
    )
    remote_filters = "\n".join(
        f'    (remote ip "{_SANDBOX_LOOPBACK_HOST}:{port}")' for port in CANDIDATE_PORTS
    )
    return f"""(version 1)
(deny default)
(allow file-read*)
(allow process*)
(allow signal)
(allow sysctl-read)
(allow mach-lookup)
(allow ipc-posix*)
(allow ipc-sysv*)
(allow file-write* (subpath {root_literal}))
(allow file-write* (literal "/dev/null"))
(allow network-bind
{local_filters})
(allow network-inbound
{local_filters})
(allow network-outbound
{remote_filters})
"""


def sandboxed_app_argv(profile: Path, app_bundle: Path) -> tuple[str, ...]:
    """Invoke the app executable directly through the macOS sandbox boundary."""
    return (
        str(_SANDBOX_EXEC),
        "-f",
        str(profile),
        *direct_app_argv(app_bundle),
    )


def parse_process_snapshot(output: str) -> dict[int, Process]:
    """Parse ``ps`` output with PID, PPID, PGID, SID, and full command columns."""
    processes: dict[int, Process] = {}
    for line_number, raw_line in enumerate(output.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(None, 4)
        if len(fields) != _PROCESS_SNAPSHOT_FIELD_COUNT:
            msg = f"invalid ps row {line_number}: {raw_line!r}"
            raise ValidationError(msg)
        try:
            pid, ppid, pgid, sid = (int(value) for value in fields[:4])
        except ValueError as error:
            msg = f"non-numeric ps identity on row {line_number}: {raw_line!r}"
            raise ValidationError(msg) from error
        if pid in processes:
            msg = f"duplicate PID {pid} in ps snapshot"
            raise ValidationError(msg)
        processes[pid] = Process(
            pid=pid,
            ppid=ppid,
            pgid=pgid,
            sid=sid,
            command=fields[4],
        )
    return processes


def is_descendant(
    processes: Mapping[int, Process],
    *,
    pid: int,
    ancestor_pid: int,
) -> bool:
    """Return whether ``pid`` has ``ancestor_pid`` in this process snapshot."""
    seen: set[int] = set()
    current = processes.get(pid)
    while current is not None and current.pid not in seen:
        seen.add(current.pid)
        if current.ppid == ancestor_pid:
            return True
        current = processes.get(current.ppid)
    return False


def owned_process_groups(
    processes: Mapping[int, Process],
    *,
    root_pid: int,
    session_id: int,
) -> tuple[int, ...]:
    """Find descendant PGIDs proven to belong to the app's isolated session."""
    groups = {
        process.pgid
        for process in processes.values()
        if process.sid == session_id
        and (
            process.pid == root_pid
            or is_descendant(processes, pid=process.pid, ancestor_pid=root_pid)
        )
    }
    return tuple(sorted(groups))


def parse_lsof_listeners(output: str) -> tuple[Listener, ...]:
    """Parse machine-readable lsof process, command, and endpoint fields."""
    listeners: list[Listener] = []
    current_pid: int | None = None
    current_command = ""
    for raw_line in output.splitlines():
        if not raw_line:
            continue
        field, value = raw_line[0], raw_line[1:]
        if field == "p":
            try:
                current_pid = int(value)
            except ValueError as error:
                msg = f"invalid lsof PID field: {raw_line!r}"
                raise ValidationError(msg) from error
            current_command = ""
        elif field == "c":
            current_command = value
        elif field == "n" and current_pid is not None:
            address = value.removesuffix(" (LISTEN)")
            port_text = address.rsplit(":", 1)[-1]
            try:
                port = int(port_text)
            except ValueError as error:
                msg = f"invalid lsof listener endpoint: {raw_line!r}"
                raise ValidationError(msg) from error
            listeners.append(
                Listener(
                    pid=current_pid,
                    command=current_command,
                    address=address,
                    port=port,
                )
            )
    return tuple(listeners)


def isolated_environment(
    root: Path,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build and materialize the app's isolated HOME, TMPDIR, and XDG roots."""
    source = os.environ if base is None else base
    environment = {
        name: source[name]
        for name in _INHERITED_ENVIRONMENT_VARIABLES
        if name in source
    }
    environment["PATH"] = _FINDER_PATH
    home = root / "home"
    locations = {
        "HOME": home,
        "CFFIXED_USER_HOME": home,
        "TMPDIR": root / "tmp",
        "XDG_CACHE_HOME": root / "xdg/cache",
        "XDG_CONFIG_HOME": root / "xdg/config",
        "XDG_DATA_HOME": root / "xdg/data",
        "XDG_STATE_HOME": root / "xdg/state",
    }
    for path in set(locations.values()):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    environment.update({name: str(path) for name, path in locations.items()})
    environment.pop("STUDIO_HOME", None)
    environment.pop("UNSLOTH_STUDIO_HOME", None)
    environment["UNSLOTH_DISABLE_UPDATE_CHECK"] = "1"
    environment["UNSLOTH_NIX_MANAGED"] = "1"
    return environment


def _require_store_path(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        msg = f"{label} does not resolve: {path}"
        raise ValidationError(msg) from error
    try:
        relative = resolved.relative_to(_STORE_ROOT)
    except ValueError as error:
        msg = f"{label} is not in /nix/store: {resolved}"
        raise ValidationError(msg) from error
    if len(relative.parts) < 1:
        msg = f"{label} is not inside a Nix store output: {resolved}"
        raise ValidationError(msg)
    return resolved


def _load_store_evidence(smoke_result: Path) -> StoreEvidence:
    smoke = _require_store_path(smoke_result, label="storePathAppCandidateSmoke output")
    app_candidate = _require_store_path(
        smoke / "app-candidate",
        label="appCandidate evidence",
    )
    backend_evidence = smoke / "backend-path"
    try:
        backend_lines = backend_evidence.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        msg = f"cannot read backend-path evidence: {backend_evidence}"
        raise ValidationError(msg) from error
    if len(backend_lines) != 1 or not backend_lines[0]:
        msg = "backend-path evidence must contain exactly one non-empty path"
        raise ValidationError(msg)
    backend_executable = _require_store_path(
        Path(backend_lines[0]),
        label="backend executable evidence",
    )
    runtime_entrypoint_evidence = smoke / "backend-runtime-entrypoint-path"
    try:
        runtime_entrypoint_lines = runtime_entrypoint_evidence.read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError as error:
        msg = (
            "cannot read backend-runtime-entrypoint-path evidence: "
            f"{runtime_entrypoint_evidence}"
        )
        raise ValidationError(msg) from error
    if len(runtime_entrypoint_lines) != 1 or not runtime_entrypoint_lines[0]:
        msg = (
            "backend-runtime-entrypoint-path evidence must contain exactly one "
            "non-empty path"
        )
        raise ValidationError(msg)
    backend_runtime_entrypoint = _require_store_path(
        Path(runtime_entrypoint_lines[0]),
        label="backend runtime entrypoint evidence",
    )
    app_bundle = app_candidate / "Applications/Unsloth.app"
    app_executable = Path(direct_app_argv(app_bundle)[0])
    for label, executable in (
        ("app CFBundleExecutable", app_executable),
        ("backend executable", backend_executable),
        ("backend runtime entrypoint", backend_runtime_entrypoint),
    ):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            msg = f"{label} is not executable: {executable}"
            raise ValidationError(msg)
    return StoreEvidence(
        app_candidate=app_candidate,
        app_bundle=app_bundle,
        app_executable=app_executable,
        backend_executable=backend_executable,
        backend_runtime_entrypoint=backend_runtime_entrypoint,
    )


def _run_snapshot(argv: Sequence[str], *, no_rows_exit: int | None = None) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 -- argv is built from fixed tools
            argv,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        msg = f"could not execute {argv[0]}: {error}"
        raise ValidationError(msg) from error
    allowed = {0}
    if no_rows_exit is not None:
        allowed.add(no_rows_exit)
    if completed.returncode not in allowed:
        msg = (
            f"{' '.join(argv)} exited {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
        raise ValidationError(msg)
    return completed.stdout


def _listeners() -> tuple[Listener, ...]:
    output = _run_snapshot(
        (
            str(_LSOF),
            "-nP",
            "-iTCP",
            "-sTCP:LISTEN",
            "-Fpcn",
        ),
        no_rows_exit=1,
    )
    return parse_lsof_listeners(output)


def _processes() -> dict[int, Process]:
    output = _run_snapshot((
        str(_PS),
        "-ww",
        "-axo",
        "pid=,ppid=,pgid=,command=",
    ))
    rows: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(None, 3)
        if len(fields) != _RAW_PS_FIELD_COUNT:
            msg = f"invalid ps row: {raw_line!r}"
            raise ValidationError(msg)
        try:
            pid = int(fields[0])
            session_id = os.getsid(pid)
        except ProcessLookupError, ValueError:
            continue
        rows.append(f"{fields[0]} {fields[1]} {fields[2]} {session_id} {fields[3]}")
    return parse_process_snapshot("\n".join(rows))


def _listener_identity(
    listeners: Sequence[Listener],
    port: int,
) -> ListenerIdentity:
    return tuple(
        sorted(
            (listener.pid, listener.command, listener.address)
            for listener in listeners
            if listener.port == port
        )
    )


def _require_sentinel_listener(
    listeners: Sequence[Listener],
    sentinel: SentinelBaseline,
) -> None:
    actual = _listener_identity(listeners, sentinel.port)
    if actual != sentinel.identity:
        msg = f"validator sentinel listener identity changed on port {sentinel.port}"
        raise ValidationError(msg)


def _candidate_listeners(listeners: Sequence[Listener]) -> tuple[Listener, ...]:
    return tuple(listener for listener in listeners if listener.port in CANDIDATE_PORTS)


def request_candidate_health(port: int) -> object | None:
    """Read health only from the dedicated candidate port range."""
    if port not in CANDIDATE_PORTS:
        msg = f"health requests are restricted to candidate health ports: {port}"
        raise ValidationError(msg)
    connection = http.client.HTTPConnection(HEALTH_HOST, port, timeout=2)
    try:
        connection.request("GET", HEALTH_PATH, headers={"Connection": "close"})
        response = connection.getresponse()
        payload = response.read()
    except OSError, http.client.HTTPException:
        return None
    finally:
        connection.close()
    if not _HTTP_SUCCESS_MIN <= response.status < _HTTP_SUCCESS_MAX:
        return None
    try:
        return json.loads(payload)
    except UnicodeDecodeError, json.JSONDecodeError:
        return None


def backend_argv_matches_evidence(
    command: str,
    *,
    backend_runtime_entrypoint: Path,
    port: int,
) -> bool:
    """Require the exact smoke-evidenced exec target and its runtime arguments."""
    required = (
        "studio",
        "--api-only",
        "-H",
        HEALTH_HOST,
        "-p",
        str(port),
    )
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if str(backend_runtime_entrypoint) not in tokens:
        return False
    position = 0
    for token in tokens:
        if token == required[position]:
            position += 1
            if position == len(required):
                return True
    return False


def _wait_for_runtime(
    *,
    app_pid: int,
    backend_runtime_entrypoint: Path,
    session_id: int,
    sentinel: SentinelBaseline,
    timeout: float,
) -> RuntimeEvidence:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listeners = _listeners()
        _require_sentinel_listener(listeners, sentinel)
        processes = _processes()
        app_process = processes.get(app_pid)
        if app_process is None:
            msg = "app exited before runtime validation completed"
            raise ValidationError(msg)
        if app_process.sid != session_id:
            msg = "app escaped its isolated launch session"
            raise ValidationError(msg)
        candidate_listeners = _candidate_listeners(listeners)
        if len(candidate_listeners) > 1:
            ports = sorted({listener.port for listener in candidate_listeners})
            msg = f"multiple candidate listeners appeared: {ports}"
            raise ValidationError(msg)
        if len(candidate_listeners) == 1:
            listener = candidate_listeners[0]
            expected_listener_address = f"{HEALTH_HOST}:{listener.port}"
            if listener.address != expected_listener_address:
                msg = (
                    "candidate listener must bind the exact candidate loopback address "
                    f"{expected_listener_address}"
                )
                raise ValidationError(msg)
            backend_process = processes.get(listener.pid)
            if backend_process is None:
                msg = "candidate listener PID is absent from ps"
                raise ValidationError(msg)
            if backend_process.sid != session_id:
                msg = "backend listener escaped the isolated app session"
                raise ValidationError(msg)
            if not is_descendant(
                processes,
                pid=backend_process.pid,
                ancestor_pid=app_pid,
            ):
                msg = "backend listener is not a descendant of the app"
                raise ValidationError(msg)
            if not backend_argv_matches_evidence(
                backend_process.command,
                backend_runtime_entrypoint=backend_runtime_entrypoint,
                port=listener.port,
            ):
                msg = "listener process does not match backend argv"
                raise ValidationError(msg)
            payload = request_candidate_health(listener.port)
            health = required_health_evidence(payload)
            if health is not None:
                groups = owned_process_groups(
                    processes,
                    root_pid=app_pid,
                    session_id=session_id,
                )
                if app_process.pgid not in groups or backend_process.pgid not in groups:
                    msg = "app or backend process group was not captured"
                    raise ValidationError(msg)
                return RuntimeEvidence(
                    app_pid=app_pid,
                    backend_pid=backend_process.pid,
                    health=health,
                    listener_address=listener.address,
                    owned_process_groups=groups,
                    port=listener.port,
                    session_id=session_id,
                )
        time.sleep(0.25)
    msg = "timed out waiting for the contained Unsloth backend"
    raise ValidationError(msg)


def _groups_in_session(
    processes: Mapping[int, Process], session_id: int
) -> tuple[int, ...]:
    return tuple(
        sorted({
            process.pgid for process in processes.values() if process.sid == session_id
        })
    )


def _signal_session_groups(session_id: int, sig: signal.Signals) -> tuple[int, ...]:
    groups = _groups_in_session(_processes(), session_id)
    for pgid in reversed(groups):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            continue
    return groups


def _session_is_gone(session_id: int) -> bool:
    return not any(process.sid == session_id for process in _processes().values())


def _signal_spawned_app(app: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    """Signal only the exact successful Popen when it remains live."""
    if app.poll() is not None:
        return
    try:
        if sig == signal.SIGTERM:
            app.terminate()
        else:
            app.kill()
    except ProcessLookupError:
        return


def _teardown_session(
    *,
    app: subprocess.Popen[bytes],
    session_id: int,
    sentinel: SentinelBaseline,
    timeout: float,
) -> None:
    verification_errors: list[ValidationError] = []

    def remember(error: ValidationError) -> None:
        if not any(str(existing) == str(error) for existing in verification_errors):
            verification_errors.append(error)

    def signal_session_groups(sig: signal.Signals) -> tuple[int, ...]:
        try:
            return _signal_session_groups(session_id, sig)
        except ValidationError as error:
            remember(error)
            return ()

    def session_is_gone() -> bool:
        try:
            return _session_is_gone(session_id)
        except ValidationError as error:
            remember(error)
            return False

    _signal_spawned_app(app, signal.SIGTERM)
    signal_session_groups(signal.SIGTERM)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app_exit = app.poll()
        try:
            listeners = _listeners()
            _require_sentinel_listener(listeners, sentinel)
        except ValidationError as error:
            remember(error)
            listeners = ()
        if (
            app_exit is not None
            and session_is_gone()
            and not _candidate_listeners(listeners)
        ):
            if verification_errors:
                raise verification_errors[0]
            return
        signal_session_groups(signal.SIGTERM)
        time.sleep(0.1)

    _signal_spawned_app(app, signal.SIGKILL)
    remaining_groups = signal_session_groups(signal.SIGKILL)
    cleanup_deadline = time.monotonic() + _FORCED_CLEANUP_TIMEOUT_SECONDS
    while time.monotonic() < cleanup_deadline:
        app_exit = app.poll()
        try:
            candidate_listeners = _candidate_listeners(_listeners())
        except ValidationError:
            candidate_listeners = ()
        if app_exit is not None and session_is_gone() and not candidate_listeners:
            break
        time.sleep(0.1)
    try:
        _require_sentinel_listener(_listeners(), sentinel)
    except ValidationError as error:
        remember(error)
    details = "; ".join(str(error) for error in verification_errors)
    suffix = f"; {details}" if details else ""
    msg = (
        "contained runtime required forced teardown of process groups "
        f"{remaining_groups}{suffix}"
    )
    raise ValidationError(msg)


def _require_runtime_parameters(
    startup_timeout: float, teardown_timeout: float
) -> None:
    if startup_timeout <= 0 or teardown_timeout <= 0:
        msg = "timeouts must be positive"
        raise ValidationError(msg)
    if tuple(range(8888, 8909)) != CANDIDATE_PORTS:
        msg = "candidate port contract is internally inconsistent"
        raise ValidationError(msg)


def _close_sentinel_after_setup_failure(
    listener: socket.socket | None,
    error: BaseException,
) -> None:
    if listener is None:
        return
    try:
        listener.close()
    except OSError as cleanup_error:
        error.add_note(f"sentinel socket cleanup also failed: {cleanup_error}")


def _sentinel_socket() -> tuple[socket.socket, int]:
    """Bind a validator-owned IPv4 listener outside the candidate port range."""
    for _attempt in range(_SENTINEL_BIND_ATTEMPTS):
        listener: socket.socket | None = None
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind((HEALTH_HOST, 0))
            bound_address = listener.getsockname()
        except OSError as error:
            _close_sentinel_after_setup_failure(listener, error)
            msg = f"could not establish validator listener sentinel: {error}"
            raise ValidationError(msg) from error
        port = bound_address[1]
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 0 < port <= _MAX_TCP_PORT
        ):
            error = ValidationError(f"OS assigned an invalid sentinel port: {port!r}")
            _close_sentinel_after_setup_failure(listener, error)
            raise error
        if port in CANDIDATE_PORTS:
            try:
                listener.close()
            except OSError as error:
                msg = f"could not release reserved candidate port {port}: {error}"
                raise ValidationError(msg) from error
            continue
        try:
            listener.listen(1)
        except OSError as error:
            _close_sentinel_after_setup_failure(listener, error)
            msg = f"could not establish validator listener sentinel: {error}"
            raise ValidationError(msg) from error
        return listener, port
    msg = "OS repeatedly assigned sentinel ports reserved for the candidate runtime"
    raise ValidationError(msg)


def sentinel_listener_baseline(
    listeners: Sequence[Listener],
    *,
    port: int,
    owner_pid: int,
) -> ListenerIdentity:
    """Require the exact validator-owned IPv4 listener after binding it."""
    sentinel_identity = _listener_identity(listeners, port)
    if not sentinel_identity:
        msg = f"validator sentinel on port {port} is absent from the listener snapshot"
        raise ValidationError(msg)
    expected_address = f"{HEALTH_HOST}:{port}"
    if any(
        address != expected_address for _pid, _command, address in sentinel_identity
    ):
        msg = f"validator sentinel must bind exactly {expected_address}"
        raise ValidationError(msg)
    if len(sentinel_identity) != 1 or sentinel_identity[0][0] != owner_pid:
        msg = "validator sentinel listener is not owned exclusively by this validator"
        raise ValidationError(msg)
    return sentinel_identity


def _listener_baseline(port: int) -> SentinelBaseline:
    initial_listeners = _listeners()
    occupied = sorted({
        listener.port for listener in _candidate_listeners(initial_listeners)
    })
    if occupied:
        msg = f"candidate ports must all be free before launch; occupied: {occupied}"
        raise ValidationError(msg)
    sentinel_identity = sentinel_listener_baseline(
        initial_listeners,
        port=port,
        owner_pid=os.getpid(),
    )
    sentinel_digest = hashlib.sha256(
        json.dumps(sentinel_identity, separators=(",", ":")).encode()
    ).hexdigest()
    return SentinelBaseline(
        port=port,
        identity=sentinel_identity,
        identity_sha256=sentinel_digest,
    )


def _start_listener_sentinel() -> ListenerSentinel:
    """Create and independently observe the validator-owned listener sentinel."""
    listener, port = _sentinel_socket()
    try:
        baseline = _listener_baseline(port)
    except BaseException as error:
        try:
            listener.close()
        except OSError as cleanup_error:
            error.add_note(f"sentinel socket cleanup also failed: {cleanup_error}")
        raise
    return ListenerSentinel(listener=listener, baseline=baseline)


def _teardown_listener_sentinel(sentinel: ListenerSentinel) -> None:
    """Close the owned sentinel and prove that its listener is absent."""
    close_error: OSError | None = None
    try:
        sentinel.listener.close()
    except OSError as error:
        close_error = error
    try:
        remaining = _listener_identity(_listeners(), sentinel.baseline.port)
    except ValidationError as inspection_error:
        if close_error is not None:
            inspection_error.add_note(
                f"sentinel socket close also failed: {close_error}"
            )
        raise
    if remaining:
        msg = f"validator sentinel on port {sentinel.baseline.port} survived teardown"
        error = ValidationError(msg)
        if close_error is not None:
            error.add_note(f"sentinel socket close also failed: {close_error}")
        raise error from close_error
    if close_error is not None:
        msg = f"could not close validator listener sentinel: {close_error}"
        raise ValidationError(msg) from close_error


def _launch_direct_app(
    *,
    store: StoreEvidence,
    environment: Mapping[str, str],
    log: IO[bytes],
    profile: Path,
) -> subprocess.Popen[bytes]:
    try:
        app = subprocess.Popen(  # noqa: S603 -- executable is a verified store path
            sandboxed_app_argv(profile, store.app_bundle),
            cwd=store.app_candidate,
            env=environment,
            start_new_session=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    except OSError as error:
        msg = f"could not launch direct app executable: {error}"
        raise ValidationError(msg) from error
    return app


def _isolated_session_id(app: subprocess.Popen[bytes]) -> int:
    """Verify the successful spawn became the promised isolated session leader."""
    try:
        session_id = os.getsid(app.pid)
    except ProcessLookupError as error:
        msg = "could not verify the direct app isolated session"
        raise ValidationError(msg) from error
    if session_id == app.pid:
        return session_id
    msg = "direct app did not become its isolated session leader"
    raise ValidationError(msg)


def _run_contained_runtime(
    *,
    store: StoreEvidence,
    sentinel: SentinelBaseline,
    startup_timeout: float,
    teardown_timeout: float,
) -> tuple[RuntimeEvidence, int]:
    evidence: RuntimeEvidence | None = None
    validation_error: ValidationError | None = None
    teardown_error: ValidationError | None = None

    with tempfile.TemporaryDirectory(prefix="unsloth-store-runtime-") as temporary:
        root = Path(temporary)
        environment = isolated_environment(root)
        profile = root / "containment.sb"
        profile.write_text(sandbox_profile(root), encoding="utf-8")
        log_path = root / "app.log"
        with log_path.open("wb") as log:
            app = _launch_direct_app(
                store=store,
                environment=environment,
                log=log,
                profile=profile,
            )
            session_id = app.pid
            try:
                try:
                    session_id = _isolated_session_id(app)
                    evidence = _wait_for_runtime(
                        app_pid=app.pid,
                        backend_runtime_entrypoint=store.backend_runtime_entrypoint,
                        session_id=session_id,
                        sentinel=sentinel,
                        timeout=startup_timeout,
                    )
                except ValidationError as error:
                    validation_error = error
            finally:
                try:
                    _teardown_session(
                        app=app,
                        session_id=session_id,
                        sentinel=sentinel,
                        timeout=teardown_timeout,
                    )
                except ValidationError as error:
                    teardown_error = error

    if validation_error is not None:
        if teardown_error is not None:
            msg = f"{validation_error}; teardown also failed: {teardown_error}"
            raise ValidationError(msg) from validation_error
        raise validation_error
    if teardown_error is not None:
        raise teardown_error
    if evidence is None:
        msg = "runtime evidence was not captured"
        raise ValidationError(msg)
    return evidence, session_id


def _require_final_teardown(
    *,
    session_id: int,
    sentinel: SentinelBaseline,
) -> None:
    final_listeners = _listeners()
    _require_sentinel_listener(final_listeners, sentinel)
    if _candidate_listeners(final_listeners):
        msg = "candidate listener survived contained teardown"
        raise ValidationError(msg)
    if not _session_is_gone(session_id):
        msg = "app or backend process survived contained teardown"
        raise ValidationError(msg)


def validate_store_runtime(
    smoke_result: Path,
    *,
    startup_timeout: float = 300,
    teardown_timeout: float = 30,
) -> dict[str, object]:
    """Run the direct store-path app gate and return machine-readable evidence."""
    _require_runtime_parameters(startup_timeout, teardown_timeout)
    store = _load_store_evidence(smoke_result)
    listener_sentinel = _start_listener_sentinel()
    try:
        evidence, session_id = _run_contained_runtime(
            store=store,
            sentinel=listener_sentinel.baseline,
            startup_timeout=startup_timeout,
            teardown_timeout=teardown_timeout,
        )
        _require_final_teardown(
            session_id=session_id,
            sentinel=listener_sentinel.baseline,
        )
    except BaseException as error:
        try:
            _teardown_listener_sentinel(listener_sentinel)
        except ValidationError as cleanup_error:
            if isinstance(error, ValidationError):
                msg = (
                    f"{error}; sentinel teardown also failed: "
                    f"{_error_message(cleanup_error)}"
                )
                raise ValidationError(msg) from error
            error.add_note(
                f"sentinel teardown also failed: {_error_message(cleanup_error)}"
            )
        raise
    _teardown_listener_sentinel(listener_sentinel)

    return {
        "appCandidate": str(store.app_candidate),
        "appPid": evidence.app_pid,
        "backendPid": evidence.backend_pid,
        "backendExecutable": str(store.backend_executable),
        "backendRuntimeEntrypoint": str(store.backend_runtime_entrypoint),
        "health": evidence.health,
        "listenerAddress": evidence.listener_address,
        "listenerOwnership": "passed",
        "ownedProcessGroups": list(evidence.owned_process_groups),
        "port": evidence.port,
        "protectedListenerCount": len(listener_sentinel.baseline.identity),
        "protectedListenerIdentitySha256": (listener_sentinel.baseline.identity_sha256),
        "sandbox": "passed",
        "schemaVersion": 2,
        "sessionId": evidence.session_id,
        "status": "passed",
        "teardown": "passed",
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the host-runtime validation CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-result",
        required=True,
        type=Path,
        help="realized storePathAppCandidateSmoke output path",
    )
    parser.add_argument("--startup-timeout", default=300, type=float)
    parser.add_argument("--teardown-timeout", default=30, type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the host gate and print one JSON evidence object on success."""
    args = build_parser().parse_args(argv)
    try:
        evidence = validate_store_runtime(
            args.smoke_result,
            startup_timeout=args.startup_timeout,
            teardown_timeout=args.teardown_timeout,
        )
    except ValidationError as error:
        msg = f"Unsloth store runtime validation failed: {_error_message(error)}"
        raise SystemExit(msg) from error
    sys.stdout.write(f"{json.dumps(evidence, sort_keys=True)}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover -- direct CLI guard delegates to main
    raise SystemExit(main())

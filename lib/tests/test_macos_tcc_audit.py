"""Tests for :mod:`lib.macos.tcc_audit`."""

import json
import plistlib
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import Callable

# The pyobjc frameworks this package drives are Darwin-only dependencies (see
# pyproject), and the Linux validators run this same suite. Skip before the
# imports below run, so the module reports as skipped instead of failing
# collection on a platform where System Settings cannot exist.
if sys.platform != "darwin":
    pytest.skip(
        "The TCC auditor drives macOS System Settings through pyobjc frameworks.",
        allow_module_level=True,
    )

from lib.macos.tcc_audit import (
    accessibility,
    cli,
    removal,
    settings,
)
from lib.macos.tcc_audit import (
    bundles as bundles_module,
)
from lib.macos.tcc_audit.audit import (
    Auditor,
    DeadPath,
    HiddenOrphan,
    Live,
    NameAmbiguous,
    PolicyKept,
    SystemEntry,
)
from lib.macos.tcc_audit.bundles import BundleIndex
from lib.macos.tcc_audit.db import (
    TccSnapshot,
    UnrecognizedEntry,
    read_entries,
    read_snapshot,
)
from lib.macos.tcc_audit.model import (
    Auth,
    Bundle,
    ClientKind,
    Pane,
    Service,
    TCCEntry,
    pane_label,
)
from lib.macos.tcc_audit.removal import (
    NotFound,
    Pressed,
    PressFailed,
    Refused,
    RemoveButtonMissing,
    SelectFailed,
    Selection,
)

FDA = Service.FULL_DISK_ACCESS
ACCESSIBILITY = Service.ACCESSIBILITY
SCREEN_RECORDING = Service.SCREEN_RECORDING


@pytest.fixture
def tcc_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "TCC.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE access ("
        "service TEXT, client_type INTEGER, auth_value INTEGER, client TEXT)",
    )
    connection.commit()
    connection.close()
    return db_path


def insert_row(
    db_path: Path,
    *,
    service: str,
    client_type: int,
    auth_value: int,
    client: str,
) -> None:
    connection = sqlite3.connect(db_path)
    with connection:
        connection.execute(
            "INSERT INTO access VALUES (?, ?, ?, ?)",
            (service, client_type, auth_value, client),
        )
    connection.close()


def make_entry(
    client: str,
    *,
    kind: ClientKind = ClientKind.PATH,
    service: Service = FDA,
    auth: Auth = Auth.ALLOWED,
) -> TCCEntry:
    return TCCEntry(service=service, kind=kind, auth=auth, client=client)


def make_index(
    bundles: dict[str, str],
    tmp_path: Path,
) -> BundleIndex:
    by_id = {
        bundle_id: Bundle(
            bundle_id=bundle_id,
            name=name,
            path=tmp_path / f"{name}.app",
        )
        for bundle_id, name in bundles.items()
    }
    return BundleIndex(by_id=by_id)


def never_registered(bundle_id: str) -> bool:
    return False


def always_registered(bundle_id: str) -> bool:
    return True


def auditor_for(
    index: BundleIndex,
    registered: Callable[[str], bool] = never_registered,
) -> Auditor:
    return Auditor(index=index, registered=registered)


# --- db.py -------------------------------------------------------------------


def test_read_entries_roundtrips_known_rows(tcc_db: Path) -> None:
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=ClientKind.PATH.value,
        auth_value=Auth.ALLOWED.value,
        client="/usr/local/rippling/daemon",
    )
    insert_row(
        tcc_db,
        service=ACCESSIBILITY.value,
        client_type=ClientKind.BUNDLE_ID.value,
        auth_value=Auth.DENIED.value,
        client="com.example.app",
    )
    entries = read_entries(tcc_db)
    assert entries == [
        TCCEntry(
            service=ACCESSIBILITY,
            kind=ClientKind.BUNDLE_ID,
            auth=Auth.DENIED,
            client="com.example.app",
        ),
        TCCEntry(
            service=FDA,
            kind=ClientKind.PATH,
            auth=Auth.ALLOWED,
            client="/usr/local/rippling/daemon",
        ),
    ]


def test_read_entries_skips_unknown_values(tcc_db: Path) -> None:
    insert_row(
        tcc_db,
        service="kTCCServiceFromTheFuture",
        client_type=ClientKind.PATH.value,
        auth_value=Auth.ALLOWED.value,
        client="/somewhere",
    )
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=9,
        auth_value=Auth.ALLOWED.value,
        client="/somewhere",
    )
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=ClientKind.PATH.value,
        auth_value=7,
        client="/somewhere",
    )
    assert read_entries(tcc_db) == []


# --- bundles.py --------------------------------------------------------------


def test_bundle_index_scans_app_roots(tmp_path: Path) -> None:
    good = tmp_path / "Foo.app" / "Contents"
    good.mkdir(parents=True)
    (good / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "com.example.foo",
                "CFBundleName": "Foo",
            },
        ),
    )
    index = BundleIndex.scan([tmp_path])
    carrier = index.carrier("com.example.foo")
    assert carrier is not None
    assert carrier.name == "Foo"
    assert "com.example.missing" not in index


def test_bundle_index_skips_plists_without_bundle_id(tmp_path: Path) -> None:
    contents = tmp_path / "Bad.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "Bad"}))
    index = BundleIndex.scan([tmp_path], extra_globs=[])
    assert len(index.by_id) == 0


# --- audit.py ----------------------------------------------------------------


def test_system_entries_are_never_candidates(tmp_path: Path) -> None:
    index = make_index({}, tmp_path)
    auditor = auditor_for(index)
    verdicts = auditor.classify(
        [
            make_entry("/System/Library/daemons/x"),
            make_entry("com.apple.finder", kind=ClientKind.BUNDLE_ID),
        ],
    )
    assert all(isinstance(v, SystemEntry) for v in verdicts)


def test_live_path_row_verdict(tmp_path: Path) -> None:
    binary = tmp_path / "bin"
    binary.write_bytes(b"")
    index = make_index({}, tmp_path)
    auditor = auditor_for(index)
    (verdict,) = auditor.classify([make_entry(str(binary))])
    assert isinstance(verdict, Live)
    assert verdict.name == "bin"


def test_unique_dead_path_is_actionable(tmp_path: Path) -> None:
    index = make_index({}, tmp_path)
    auditor = auditor_for(index)
    (verdict,) = auditor.classify([make_entry("/nowhere/unique-tool")])
    assert isinstance(verdict, DeadPath)
    assert verdict.name == "unique-tool"
    assert auditor.actionable([verdict]) == [verdict]


def test_shared_name_dead_paths_are_ambiguous(tmp_path: Path) -> None:
    index = make_index({}, tmp_path)
    auditor = auditor_for(index)
    verdicts = auditor.classify(
        [
            make_entry("/nix/store/aaa-nix/bin/nix"),
            make_entry("/nix/store/bbb-nix/bin/nix"),
        ],
    )
    assert all(isinstance(v, NameAmbiguous) for v in verdicts)
    assert all(v.occurrences == 2 for v in verdicts)


def test_dead_path_sharing_a_live_name_is_ambiguous(tmp_path: Path) -> None:
    live = tmp_path / "dust"
    live.write_bytes(b"")
    index = make_index({}, tmp_path)
    auditor = auditor_for(index)
    verdicts = auditor.classify([make_entry(str(live)), make_entry("/gone/dust")])
    assert isinstance(verdicts[0], Live)
    assert isinstance(verdicts[1], NameAmbiguous)
    assert verdicts[1].occurrences == 2


def test_unregistered_bundle_id_is_hidden_orphan(tmp_path: Path) -> None:
    index = make_index({}, tmp_path)
    auditor = auditor_for(index)
    (verdict,) = auditor.classify(
        [
            make_entry(
                "com.daisydiskapp.DaisyDisk",
                kind=ClientKind.BUNDLE_ID,
            ),
        ],
    )
    assert isinstance(verdict, HiddenOrphan)


def test_registered_bundle_without_carrier_is_live(tmp_path: Path) -> None:
    index = make_index({}, tmp_path)
    auditor = auditor_for(index, registered=always_registered)
    (verdict,) = auditor.classify(
        [
            make_entry(
                "com.some.registered",
                kind=ClientKind.BUNDLE_ID,
            ),
        ],
    )
    assert isinstance(verdict, Live)
    assert verdict.name == "com.some.registered"


def test_rebranded_app_resolves_by_carrier_name(tmp_path: Path) -> None:
    index = make_index({"com.openai.codex": "ChatGPT"}, tmp_path)
    auditor = auditor_for(index)
    (verdict,) = auditor.classify(
        [
            make_entry(
                "com.openai.codex",
                kind=ClientKind.BUNDLE_ID,
            ),
        ],
    )
    assert isinstance(verdict, Live)
    assert verdict.name == "ChatGPT"


def test_policy_keep_prefix_protects_stale_entry(tmp_path: Path) -> None:
    index = make_index({}, tmp_path)
    auditor = auditor_for(index)
    (verdict,) = auditor.classify(
        [
            make_entry(
                "com.rippling.mdm",
                kind=ClientKind.BUNDLE_ID,
            ),
        ],
    )
    assert isinstance(verdict, PolicyKept)
    assert verdict.reason == "company-managed identity"


def test_policy_keep_bundle_id_protects_stale_entry(tmp_path: Path) -> None:
    index = make_index({}, tmp_path)
    auditor = auditor_for(index)
    (verdict,) = auditor.classify(
        [
            make_entry(
                "com.openai.sky.CUAService",
                kind=ClientKind.BUNDLE_ID,
            ),
        ],
    )
    assert isinstance(verdict, PolicyKept)
    assert verdict.reason == "known-keep identity"


def test_remove_and_verify_reports_removed_once_row_disappears(
    tmp_path: Path,
) -> None:
    class SelfRemovingProcess(_FakeProcess):
        def press(self, element: object) -> bool:
            ok = super().press(element)
            if ok:
                self._rows = [r for r in self._rows if r.name != "Screen Tool"]
            return ok

    process = SelfRemovingProcess([_row("Screen Tool")])
    outcome = removal.remove_and_verify(
        process,
        Pane.SCREEN_RECORDING,
        "Screen Tool",
        interval=0.01,
    )
    assert isinstance(outcome, removal.Removed)
    assert outcome.name == "Screen Tool"


def test_remove_and_verify_reports_still_present_on_timeout(
    tmp_path: Path,
) -> None:
    process = _FakeProcess([_row("Screen Tool")])
    outcome = removal.remove_and_verify(
        process,
        Pane.SCREEN_RECORDING,
        "Screen Tool",
        timeout=0.05,
        interval=0.01,
    )
    assert isinstance(outcome, removal.StillPresent)


def test_remove_and_verify_dry_run_never_presses(tmp_path: Path) -> None:
    process = _FakeProcess([_row("Screen Tool")])
    outcome = removal.remove_and_verify(
        process,
        Pane.SCREEN_RECORDING,
        "Screen Tool",
        dry_run=True,
    )
    assert isinstance(outcome, removal.Selection)
    assert process.pressed == []


def test_registered_lookup_is_used_only_for_bundle_clients(
    tmp_path: Path,
) -> None:
    index = make_index({}, tmp_path)
    calls: list[str] = []

    def recording(bundle_id: str) -> bool:
        calls.append(bundle_id)
        return True

    auditor = Auditor(index=index, registered=recording)
    verdicts = auditor.classify(
        [
            make_entry("/nowhere/dead-tool"),
            make_entry("com.example.bundle", kind=ClientKind.BUNDLE_ID),
        ],
    )
    assert all(isinstance(v, (Live, DeadPath)) for v in verdicts)
    assert set(calls) == {"com.example.bundle"}


def test_service_pane_mapping_covers_removal_targets() -> None:
    """Every service the model knows must map to a pane `clean` can open.

    A service without a pane mapping is reported actionable by `audit` and
    then silently skipped by `clean`, so the mapping must stay total.
    """
    from lib.macos.tcc_audit.model import PANE_BY_SERVICE

    assert set(PANE_BY_SERVICE) == set(Service)
    assert PANE_BY_SERVICE[Service.FULL_DISK_ACCESS].value == "Privacy_AllFiles"
    assert PANE_BY_SERVICE[Service.ACCESSIBILITY].value == "Privacy_Accessibility"
    assert PANE_BY_SERVICE[Service.SCREEN_RECORDING].value == "Privacy_ScreenCapture"
    assert PANE_BY_SERVICE[Service.INPUT_MONITORING].value == "Privacy_ListenEvent"
    assert PANE_BY_SERVICE[Service.POST_EVENT].value == "Privacy_PostEvent"
    assert PANE_BY_SERVICE[Service.DEVELOPER_TOOL].value == "Privacy_DeveloperTool"


def test_pane_labels_are_the_ax_titles_system_settings_shows() -> None:
    """Every pane must carry the AX title the removal path compares against.

    ``wait_for_pane`` and ``select`` fail closed when the visible title does
    not match this table, so a missing pane would refuse every removal.
    """
    from lib.macos.tcc_audit.model import PANE_LABELS

    assert PANE_LABELS == {
        Pane.FULL_DISK_ACCESS: "Full Disk Access",
        Pane.ACCESSIBILITY: "Accessibility",
        Pane.SCREEN_RECORDING: "Screen Recording",
        Pane.INPUT_MONITORING: "Input Monitoring",
        Pane.POST_EVENT: "Send Keystrokes",
        Pane.DEVELOPER_TOOL: "Developer Tools",
    }
    assert pane_label(Pane.SCREEN_RECORDING) == "Screen Recording"


# --- removal.py ---------------------------------------------------------------


class _FakeProcess:
    def __init__(
        self,
        rows: list[object],
        *,
        displays: Pane = Pane.SCREEN_RECORDING,
        select_ok: bool = True,
        remove_button: object = "Remove",
        press_ok: bool = True,
    ) -> None:
        self._rows = list(rows)
        self._displays = displays
        self._select_ok = select_ok
        self._remove_button = remove_button
        self._press_ok = press_ok
        self.selected: list[object] = []
        self.pressed: list[object] = []

    def pane_title(self) -> str:
        """Report the requested pane unless a test says another one shows."""
        return pane_label(self._displays)

    def rows(self) -> list[object]:
        return self._rows

    def select(self, row: object) -> bool:
        self.selected.append(row)
        return self._select_ok

    def button_named(self, _name: str) -> object:
        return self._remove_button

    def press(self, element: object) -> bool:
        self.pressed.append(element)
        return self._press_ok


def _row(name: str) -> object:
    return SimpleNamespace(name=name, allowed=True, selected=False, element=object())


def test_select_returns_selection_for_unique_row() -> None:
    process = _FakeProcess([_row("CleanShot X")])
    outcome = removal.select(process, Pane.SCREEN_RECORDING, "CleanShot X")
    assert isinstance(outcome, Selection)


def test_select_refuses_ambiguous_rows() -> None:
    process = _FakeProcess([_row("ChatGPT"), _row("ChatGPT")])
    outcome = removal.select(process, Pane.SCREEN_RECORDING, "ChatGPT")
    assert isinstance(outcome, Refused)
    assert outcome.occurrences == 2


def test_select_reports_not_found() -> None:
    process = _FakeProcess([_row("CleanShot X")])
    outcome = removal.select(process, Pane.SCREEN_RECORDING, "Ghost")
    assert isinstance(outcome, NotFound)
    assert outcome.name == "Ghost"


def test_select_reports_select_failure() -> None:
    process = _FakeProcess([_row("CleanShot X")], select_ok=False)
    outcome = removal.select(process, Pane.SCREEN_RECORDING, "CleanShot X")
    assert isinstance(outcome, SelectFailed)


def test_remove_presses_button_and_returns_pressed() -> None:
    process = _FakeProcess([_row("Screen Tool")])
    outcome = removal.remove(process, Pane.SCREEN_RECORDING, "Screen Tool")
    assert isinstance(outcome, Pressed)
    assert len(process.pressed) == 1


def test_remove_dry_run_selects_without_pressing() -> None:
    process = _FakeProcess([_row("Screen Tool")])
    outcome = removal.remove(
        process, Pane.SCREEN_RECORDING, "Screen Tool", dry_run=True
    )
    assert isinstance(outcome, Selection)
    assert process.pressed == []


def test_remove_reports_missing_button() -> None:
    process = _FakeProcess([_row("Screen Tool")], remove_button=None)
    outcome = removal.remove(process, Pane.SCREEN_RECORDING, "Screen Tool")
    assert isinstance(outcome, RemoveButtonMissing)


def test_remove_reports_press_failure() -> None:
    process = _FakeProcess([_row("Screen Tool")], press_ok=False)
    outcome = removal.remove(process, Pane.SCREEN_RECORDING, "Screen Tool")
    assert isinstance(outcome, PressFailed)


def test_select_refuses_a_row_while_another_pane_is_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pane check runs before a row is read, let alone selected.

    ``rows()`` walks the whole application AX tree, so a row name that also
    exists in another pane would otherwise be selected and removed while the
    user is looking somewhere else.
    """
    monkeypatch.setattr(settings, "time", _Clock())
    process = _FakeProcess([_row("Screen Tool")], displays=Pane.ACCESSIBILITY)
    outcome = removal.select(process, Pane.SCREEN_RECORDING, "Screen Tool")
    assert isinstance(outcome, removal.WrongPane)
    assert outcome.observed == pane_label(Pane.ACCESSIBILITY)
    assert process.selected == []


def test_remove_refuses_before_selecting_on_the_wrong_pane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "time", _Clock())
    process = _FakeProcess([_row("Screen Tool")], displays=Pane.INPUT_MONITORING)
    outcome = removal.remove(process, Pane.SCREEN_RECORDING, "Screen Tool")
    assert isinstance(outcome, removal.WrongPane)
    assert outcome.observed == pane_label(Pane.INPUT_MONITORING)
    assert process.selected == []
    assert process.pressed == []


def test_remove_and_verify_refuses_on_the_wrong_pane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "time", _Clock())
    process = _FakeProcess([_row("Screen Tool")], displays=Pane.POST_EVENT)
    outcome = removal.remove_and_verify(
        process,
        Pane.SCREEN_RECORDING,
        "Screen Tool",
    )
    assert isinstance(outcome, removal.WrongPane)
    assert process.selected == []
    assert process.pressed == []


def test_select_retries_a_stale_pane_title_before_acting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A window that has not repainted yet is retried, not acted on."""
    clock = _Clock()
    monkeypatch.setattr(settings, "time", clock)
    titles = iter([pane_label(Pane.ACCESSIBILITY), pane_label(Pane.SCREEN_RECORDING)])

    class Repainting(_FakeProcess):
        def pane_title(self) -> str:
            return next(titles)

    process = Repainting([_row("Screen Tool")])
    outcome = removal.select(process, Pane.SCREEN_RECORDING, "Screen Tool")
    assert isinstance(outcome, Selection)
    assert clock.sleeps == [0.25]


def test_bundle_index_extra_globs_find_claude_code_carrier(tmp_path: Path) -> None:
    contents = (
        tmp_path / "Claude" / "claude-code" / "2.1.284" / "claude.app" / "Contents"
    )
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "com.anthropic.claude-code",
                "CFBundleName": "claude",
            },
        ),
    )
    index = BundleIndex.scan(
        roots=[],
        extra_globs=[str(tmp_path / "Claude" / "claude-code" / "*" / "claude.app")],
    )
    carrier = index.carrier("com.anthropic.claude-code")
    assert carrier is not None
    assert carrier.name == "claude"


# --- db.py snapshot (unrecognized-row surfacing) -------------------------------


def test_read_snapshot_surfaces_unrecognized_rows(tcc_db: Path) -> None:
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=ClientKind.PATH.value,
        auth_value=Auth.ALLOWED.value,
        client="/known",
    )
    insert_row(
        tcc_db,
        service="kTCCServiceFromTheFuture",
        client_type=ClientKind.BUNDLE_ID.value,
        auth_value=Auth.ALLOWED.value,
        client="future.service",
    )
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=9,
        auth_value=Auth.ALLOWED.value,
        client="/kind",
    )
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=ClientKind.PATH.value,
        auth_value=7,
        client="/auth",
    )
    snapshot = read_snapshot(tcc_db)
    assert [entry.client for entry in snapshot.entries] == ["/known"]
    assert [(u.service_key, u.auth_value, u.client) for u in snapshot.unrecognized] == [
        ("kTCCServiceFromTheFuture", "2", "future.service"),
        (FDA.value, "7", "/auth"),
        (FDA.value, "2", "/kind"),
    ]
    assert {u.reason for u in snapshot.unrecognized} == {
        "unknown service, client kind, or auth value",
    }


def test_read_snapshot_surfaces_non_conforming_columns(tcc_db: Path) -> None:
    """A NULL or BLOB column is reported from the read seam, never raised."""
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=None,
        auth_value=Auth.ALLOWED.value,
        client="/null-kind",
    )
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=ClientKind.PATH.value,
        auth_value=None,
        client="/null-auth",
    )
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=ClientKind.PATH.value,
        auth_value=Auth.ALLOWED.value,
        client=None,
    )
    insert_row(
        tcc_db,
        service=FDA.value,
        client_type=ClientKind.PATH.value,
        auth_value=Auth.ALLOWED.value,
        client=b"\x00\x01",
    )

    snapshot = read_snapshot(tcc_db)

    assert snapshot.entries == []
    assert {(u.client, u.reason) for u in snapshot.unrecognized} == {
        ("None", "service and client must be text columns"),
        (r"b'\x00\x01'", "service and client must be text columns"),
        ("/null-kind", "client_type and auth_value must be integer columns"),
        ("/null-auth", "client_type and auth_value must be integer columns"),
    }


def test_read_snapshot_empty_database(tcc_db: Path) -> None:
    snapshot = read_snapshot(tcc_db)
    assert snapshot.entries == []
    assert snapshot.unrecognized == []


# --- cli.py (typer surface) ----------------------------------------------------


def _patch_cli_inputs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snapshot: TccSnapshot,
    index: BundleIndex,
    registered: Callable[[str], bool],
) -> None:
    """Point the CLI at fixture inputs so no test touches the real TCC database."""
    monkeypatch.setattr(cli, "read_snapshot", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(
        cli.BundleIndex,
        "scan",
        classmethod(lambda cls, *args, **kwargs: index),
    )
    monkeypatch.setattr(cli, "_registered", registered)


@pytest.fixture
def cli_audit_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> BundleIndex:
    """Stub the audit inputs so CLI tests never touch the real TCC database."""
    index = make_index({"com.example.live": "Live App"}, tmp_path)
    snapshot = TccSnapshot(
        entries=[
            TCCEntry(
                service=Service.SCREEN_RECORDING,
                kind=ClientKind.BUNDLE_ID,
                auth=Auth.ALLOWED,
                client="com.apple.finder",
            ),
            TCCEntry(
                service=SCREEN_RECORDING,
                kind=ClientKind.PATH,
                auth=Auth.ALLOWED,
                client="/gone/dead-tool",
            ),
            TCCEntry(
                service=SCREEN_RECORDING,
                kind=ClientKind.BUNDLE_ID,
                auth=Auth.ALLOWED,
                client="com.example.live",
            ),
        ],
        unrecognized=[
            UnrecognizedEntry(
                service_key="kTCCServiceFromTheFuture",
                client_type="0",
                auth_value="2",
                client="future.entry",
                reason="unknown service, client kind, or auth value",
            ),
        ],
    )
    _patch_cli_inputs(
        monkeypatch,
        snapshot=snapshot,
        index=index,
        registered=lambda _bundle_id: True,
    )
    return index


@pytest.fixture
def cli_full_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> BundleIndex:
    """Stub inputs that produce one verdict of every class `audit` renders.

    Two dead path rows share a pane so `clean` exercises its process cache,
    and the orphan/ambiguous/kept rows give the skip report something to count.
    """
    index = make_index({"com.example.live": "Live App"}, tmp_path)
    snapshot = TccSnapshot(
        entries=[
            make_entry("com.apple.finder", kind=ClientKind.BUNDLE_ID),
            make_entry("com.example.live", kind=ClientKind.BUNDLE_ID),
            make_entry("/gone/dead-tool", service=SCREEN_RECORDING),
            make_entry("/gone/second-tool", service=SCREEN_RECORDING),
            make_entry("com.example.orphan", kind=ClientKind.BUNDLE_ID),
            make_entry("/gone/Live App"),
            make_entry("com.rippling.mdm-tool", kind=ClientKind.BUNDLE_ID),
        ],
        unrecognized=[
            UnrecognizedEntry(
                service_key="kTCCServiceFromTheFuture",
                client_type="0",
                auth_value="2",
                client="future.entry",
                reason="unknown service, client kind, or auth value",
            ),
        ],
    )
    _patch_cli_inputs(
        monkeypatch,
        snapshot=snapshot,
        index=index,
        registered=lambda bundle_id: bundle_id == "com.example.live",
    )
    return index


def test_cli_audit_json_lists_stale_and_unrecognized(
    cli_audit_env: BundleIndex,
) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["audit", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    statuses = {row["status"] for row in payload["rows"]}
    assert "dead-path" in statuses
    assert "unrecognized" in statuses
    assert payload["actionable"] == 1


def test_cli_audit_table_renders_headers(cli_audit_env: BundleIndex) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["audit"])
    assert result.exit_code == 0
    assert "macOS TCC privacy lists" in result.stdout
    assert "dead-path" in result.stdout
    assert "unrecognized" in result.stdout


def test_cli_audit_all_includes_system_rows(cli_audit_env: BundleIndex) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["audit", "--all", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    statuses = {row["status"] for row in payload["rows"]}
    assert "system" in statuses
    assert "live" in statuses


def test_cli_clean_dry_run_selects_without_pressing(
    monkeypatch: pytest.MonkeyPatch,
    cli_audit_env: BundleIndex,
) -> None:
    process = _FakeProcess([_row("dead-tool")])

    class FakeSettingsProcess:
        @classmethod
        def attach(cls) -> object:
            return process

    monkeypatch.setattr(cli, "SystemSettingsProcess", FakeSettingsProcess)
    monkeypatch.setattr(
        cli.settings_module,
        "ensure_running",
        lambda pane: True,
    )
    runner = CliRunner()
    result = runner.invoke(cli.app, ["clean", "--json", "--dry-run"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "results": [
            {
                "pane": "Privacy_ScreenCapture",
                "name": "dead-tool",
                "outcome": "Selection",
                "handled": True,
            },
        ],
        "failed": 0,
        "skipped": 0,
    }


# --- removal.py (pass-through outcomes) ---------------------------------------


def test_remove_returns_a_selection_failure_unchanged() -> None:
    process = _FakeProcess([_row("CleanShot X")])
    outcome = removal.remove(process, Pane.SCREEN_RECORDING, "Ghost")
    assert isinstance(outcome, NotFound)
    assert outcome.name == "Ghost"
    assert process.pressed == []


# --- bundles.py (unreadable plists and literal extra globs) --------------------


def test_bundle_from_plist_returns_none_for_unreadable_plists(
    tmp_path: Path,
) -> None:
    unreadable_dir = tmp_path / "Directory.app" / "Contents"
    unreadable_dir.mkdir(parents=True)
    (unreadable_dir / "Info.plist").mkdir()

    assert bundles_module._bundle_from_plist(unreadable_dir / "Info.plist") is None

    corrupt = tmp_path / "Corrupt.plist"
    corrupt.write_bytes(b"not a plist")
    assert bundles_module._bundle_from_plist(corrupt) is None


def test_extra_glob_without_a_wildcard_uses_the_literal_app_path(
    tmp_path: Path,
) -> None:
    contents = tmp_path / "Literal.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {"CFBundleIdentifier": "com.example.literal", "CFBundleName": "Literal"},
        ),
    )

    index = BundleIndex.scan(
        roots=[],
        extra_globs=[str(tmp_path / "Literal.app")],
    )
    carrier = index.carrier("com.example.literal")
    assert carrier is not None
    assert carrier.path == tmp_path / "Literal.app"


def test_extra_glob_without_a_wildcard_ignores_a_missing_app(tmp_path: Path) -> None:
    index = BundleIndex.scan(
        roots=[],
        extra_globs=[str(tmp_path / "Absent.app")],
    )
    assert index.by_id == {}


# --- accessibility.py (the pyobjc seam is the module's own globals) -----------


class _Ax:
    """Stand-in AX element: attributes plus ordered children."""

    def __init__(
        self,
        role: str,
        *,
        children: list[_Ax] | None = None,
        attrs: dict[str, object] | None = None,
    ) -> None:
        self.attrs: dict[str, object] = {"AXRole": role}
        if attrs:
            self.attrs.update(attrs)
        self.children: list[_Ax] = children or []


class _AxCalls:
    """Record the AX calls the module makes through the patched globals."""

    def __init__(self) -> None:
        self.attributes: list[str] = []
        self.writes: list[tuple[object, str, object]] = []
        self.actions: list[tuple[object, str]] = []
        self.created_pids: list[int] = []


class _Clock:
    """Deterministic stand-in for the ``time`` module these modules import."""

    def __init__(self, *, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        """Return the synthetic clock reading."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance the synthetic clock instead of blocking."""
        self.sleeps.append(seconds)
        self.now += seconds


def _install_ax(
    monkeypatch: pytest.MonkeyPatch,
    *,
    copy: Callable[[_Ax, str], object] | None = None,
    set_result: int = 0,
    action_result: int = 0,
) -> _AxCalls:
    """Patch the pyobjc globals so the AX translation logic runs headless."""
    calls = _AxCalls()

    def copy_attribute(element: _Ax, attribute: str, _ignored: object) -> object:
        calls.attributes.append(attribute)
        if copy is not None:
            return copy(element, attribute)
        if attribute == "AXChildren":
            return (0, element.children) if element.children else (0, None)
        if attribute in element.attrs:
            return (0, element.attrs[attribute])
        return (1, None)

    def set_attribute(element: _Ax, attribute: str, value: object) -> int:
        calls.writes.append((element, attribute, value))
        if set_result == 0:
            element.attrs[attribute] = value
        return set_result

    def perform_action(element: _Ax, action: str) -> int:
        calls.actions.append((element, action))
        return action_result

    def create_application(pid: int) -> _Ax:
        calls.created_pids.append(pid)
        return _Ax("AXApplication")

    monkeypatch.setattr(accessibility, "AXUIElementCopyAttributeValue", copy_attribute)
    monkeypatch.setattr(accessibility, "AXUIElementSetAttributeValue", set_attribute)
    monkeypatch.setattr(accessibility, "AXUIElementPerformAction", perform_action)
    monkeypatch.setattr(
        accessibility,
        "AXUIElementCreateApplication",
        create_application,
    )
    monkeypatch.setattr(accessibility, "time", _Clock())
    return calls


def _running_applications(pids: list[int]) -> SimpleNamespace:
    """Fake ``NSRunningApplication`` exposing the queried pid."""
    apps = [SimpleNamespace(processIdentifier=lambda pid=pid: pid) for pid in pids]
    return SimpleNamespace(
        runningApplicationsWithBundleIdentifier_=lambda _bundle_id: apps,
    )


def test_copy_attribute_normalizes_pyobjc_return_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shapes: dict[object, tuple[bool, object]] = {
        (0, "value"): (True, "value"),
        (0,): (True, None),
        (5, "value"): (False, "value"),
        (): (False, None),
        None: (False, None),
        "unexpected": (False, None),
    }
    for raw, expected in shapes.items():
        _install_ax(monkeypatch, copy=lambda _element, _attribute, raw=raw: raw)
        assert accessibility._copy_attribute(object(), "AXValue") == expected


def test_set_attribute_and_perform_action_report_ax_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_ax(monkeypatch, set_result=0, action_result=0)
    element = _Ax("AXRow")
    assert accessibility.set_attribute(element, "AXSelected", value=True) is True
    assert accessibility.perform_action(element, "AXPress") is True
    assert calls.writes[0][1:] == ("AXSelected", True)
    assert element.attrs["AXSelected"] is True
    assert calls.actions[0][1] == "AXPress"

    _install_ax(monkeypatch, set_result=-25201, action_result=-25201)
    assert accessibility.set_attribute(element, "AXSelected", value=False) is False
    assert accessibility.perform_action(element, "AXPress") is False


def test_attr_uses_the_default_when_the_attribute_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _Ax("AXRow", attrs={"AXSelected": True})
    _install_ax(monkeypatch)
    assert accessibility.attr(node, "AXSelected", default=False) is True
    assert accessibility.attr(node, "AXTitle", default="missing") == "missing"


def test_walk_yields_depth_first_and_honours_the_depth_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaf = _Ax("AXStaticText", attrs={"AXValue": "Dead Tool"})
    first = _Ax("AXRow", children=[leaf])
    second = _Ax("AXRow")
    root = _Ax("AXWindow", children=[first, second])
    _install_ax(monkeypatch)

    assert [(role, depth) for _element, role, depth in accessibility.walk(root)] == [
        ("AXWindow", 0),
        ("AXRow", 1),
        ("AXStaticText", 2),
        ("AXRow", 1),
    ]
    assert [
        role for _element, role, _depth in accessibility.walk(root, max_depth=0)
    ] == [
        "AXWindow",
    ]


def test_walk_ignores_a_malformed_children_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _Ax("AXWindow")

    def copy(element: _Ax, attribute: str) -> object:
        if attribute == "AXChildren":
            return (0, "not-a-sequence")
        return (0, element.attrs.get(attribute))

    _install_ax(monkeypatch, copy=copy)
    assert [role for _element, role, _depth in accessibility.walk(node)] == ["AXWindow"]


def test_row_parts_reads_the_first_static_text_and_requires_a_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ax(monkeypatch)
    first = _Ax("AXStaticText", attrs={"AXValue": "CleanShot X"})
    later = _Ax("AXStaticText", attrs={"AXValue": "later"})

    assert accessibility._row_parts(
        _Ax(
            "AXRow",
            children=[first, later, _Ax("AXCheckBox", attrs={"AXValue": True})],
        ),
    ) == ("CleanShot X", True)
    assert accessibility._row_parts(
        _Ax("AXRow", children=[first, _Ax("AXCheckBox")]),
    ) == ("CleanShot X", None)
    assert accessibility._row_parts(
        _Ax("AXRow", children=[first, _Ax("AXCheckBox", attrs={"AXValue": False})]),
    ) == ("CleanShot X", False)
    assert accessibility._row_parts(_Ax("AXRow", children=[first])) == ("", None)
    assert accessibility._row_parts(
        _Ax("AXRow", children=[_Ax("AXCheckBox", attrs={"AXValue": True})]),
    ) == ("", True)


def test_attach_returns_none_when_system_settings_is_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ax(monkeypatch)
    monkeypatch.setattr(
        accessibility, "NSRunningApplication", _running_applications([])
    )
    assert accessibility.SystemSettingsProcess.attach() is None


def test_attach_wraps_the_running_system_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_ax(monkeypatch)
    monkeypatch.setattr(
        accessibility,
        "NSRunningApplication",
        _running_applications([4321]),
    )
    process = accessibility.SystemSettingsProcess.attach()
    assert process is not None
    assert process.pid == 4321
    assert calls.created_pids == [4321]


def test_rows_collects_toggle_rows_and_reports_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ax(monkeypatch)
    toggleless = _Ax("AXRow")
    dead = _Ax(
        "AXRow",
        children=[
            _Ax("AXStaticText", attrs={"AXValue": "Dead Tool"}),
            _Ax("AXCheckBox", attrs={"AXValue": True}),
        ],
    )
    selected = _Ax(
        "AXRow",
        attrs={"AXSelected": True},
        children=[
            _Ax("AXStaticText", attrs={"AXValue": "Live Tool"}),
            _Ax("AXCheckBox", attrs={"AXValue": False}),
        ],
    )
    root = _Ax(
        "AXWindow",
        children=[
            _Ax("AXButton", attrs={"AXTitle": "Add"}),
            toggleless,
            dead,
            selected,
        ],
    )

    rows = accessibility.SystemSettingsProcess(root, pid=99).rows()
    assert [(row.name, row.allowed, row.selected) for row in rows] == [
        ("Dead Tool", True, False),
        ("Live Tool", False, True),
    ]


def test_select_reports_whether_the_selection_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    element = _Ax("AXRow")
    row = accessibility.PaneRow(
        name="Dead Tool",
        allowed=True,
        selected=False,
        element=element,
    )
    process = accessibility.SystemSettingsProcess(_Ax("AXWindow"), 1)

    _install_ax(monkeypatch, set_result=0)
    assert process.select(row) is True
    assert element.attrs["AXSelected"] is True

    _install_ax(monkeypatch, set_result=-25201)
    assert process.select(row) is False


def test_button_named_matches_only_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ax(monkeypatch)
    target = _Ax("AXButton", attrs={"AXTitle": "Remove"})
    root = _Ax(
        "AXWindow",
        children=[
            _Ax("AXRow", attrs={"AXTitle": "Remove"}),
            _Ax("AXButton", attrs={"AXTitle": "Add"}),
            target,
        ],
    )
    process = accessibility.SystemSettingsProcess(root, 7)
    assert process.button_named("Remove") is target
    assert process.button_named("Ghost") is None


def test_press_and_pane_title_use_the_ax_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_ax(monkeypatch, action_result=0)
    process = accessibility.SystemSettingsProcess(
        _Ax("AXWindow", attrs={"AXTitle": "Screen Recording"}),
        3,
    )
    assert process.press(object()) is True
    assert calls.actions[0][1] == "AXPress"
    assert process.pane_title() == "Screen Recording"

    _install_ax(monkeypatch, action_result=-25201)
    assert process.press(object()) is False
    untitled = accessibility.SystemSettingsProcess(_Ax("AXWindow"), 3)
    assert untitled.pane_title() == ""


# --- settings.py (pane deep-links and prompt polling) -------------------------


def test_open_pane_builds_the_pane_deep_link(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []
    opened: list[object] = []
    url = object()

    def url_with_string(value: str) -> object:
        requested.append(value)
        return url

    def open_url(target: object) -> bool:
        opened.append(target)
        return True

    workspace = SimpleNamespace(openURL_=open_url)
    workspace.sharedWorkspace = lambda: workspace
    monkeypatch.setattr(
        settings,
        "NSURL",
        SimpleNamespace(URLWithString_=url_with_string),
    )
    monkeypatch.setattr(settings, "NSWorkspace", workspace)

    assert settings.open_pane(Pane.POST_EVENT) is True
    assert requested == [
        settings.PANE_URL_TEMPLATE.format(suffix=Pane.POST_EVENT.value)
    ]
    assert opened == [url]


def test_open_pane_reports_when_the_workspace_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(openURL_=lambda _target: False)
    workspace.sharedWorkspace = lambda: workspace
    monkeypatch.setattr(
        settings,
        "NSURL",
        SimpleNamespace(URLWithString_=lambda _value: object()),
    )
    monkeypatch.setattr(settings, "NSWorkspace", workspace)

    assert settings.open_pane(Pane.SCREEN_RECORDING) is False


def test_system_settings_pid_reports_absent_and_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "NSRunningApplication",
        SimpleNamespace(runningApplicationsWithBundleIdentifier_=lambda _id: []),
    )
    assert settings.system_settings_pid() is None

    monkeypatch.setattr(
        settings,
        "NSRunningApplication",
        SimpleNamespace(
            runningApplicationsWithBundleIdentifier_=lambda _id: [
                SimpleNamespace(processIdentifier=lambda: 4321),
            ],
        ),
    )
    assert settings.system_settings_pid() == 4321


def test_ensure_running_opens_the_pane_when_settings_is_already_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[Pane] = []

    def open_pane(pane: Pane) -> bool:
        opened.append(pane)
        return True

    monkeypatch.setattr(settings, "system_settings_pid", lambda: 4321)
    monkeypatch.setattr(settings, "open_pane", open_pane)

    assert settings.ensure_running(Pane.SCREEN_RECORDING) is True
    assert opened == [Pane.SCREEN_RECORDING]


def test_ensure_running_reports_when_the_pane_cannot_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "system_settings_pid", lambda: None)
    monkeypatch.setattr(settings, "open_pane", lambda _pane: False)

    assert settings.ensure_running(Pane.SCREEN_RECORDING) is False


def test_ensure_running_waits_for_the_pid_to_appear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    pids = iter([None, None, 4321])
    monkeypatch.setattr(settings, "time", clock)
    monkeypatch.setattr(settings, "open_pane", lambda _pane: True)
    monkeypatch.setattr(settings, "system_settings_pid", lambda: next(pids))

    assert settings.ensure_running(Pane.SCREEN_RECORDING, timeout=10.0) is True
    assert clock.sleeps == [0.25]


def test_ensure_running_times_out_when_the_pid_never_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(settings, "time", clock)
    monkeypatch.setattr(settings, "open_pane", lambda _pane: True)
    monkeypatch.setattr(settings, "system_settings_pid", lambda: None)

    assert settings.ensure_running(Pane.SCREEN_RECORDING, timeout=0.5) is False
    assert clock.sleeps == [0.25, 0.25]


def test_wait_for_pane_returns_the_title_once_the_requested_pane_shows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    titles = iter([pane_label(Pane.ACCESSIBILITY), pane_label(Pane.SCREEN_RECORDING)])
    monkeypatch.setattr(settings, "time", clock)
    process = SimpleNamespace(pane_title=lambda: next(titles))

    assert settings.wait_for_pane(process, Pane.SCREEN_RECORDING) == pane_label(
        Pane.SCREEN_RECORDING
    )
    assert clock.sleeps == [0.25]


def test_wait_for_pane_returns_the_last_title_when_the_pane_never_shows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(settings, "time", clock)
    process = SimpleNamespace(pane_title=lambda: pane_label(Pane.ACCESSIBILITY))

    observed = settings.wait_for_pane(
        process,
        Pane.SCREEN_RECORDING,
        timeout=0.5,
        interval=0.25,
    )

    assert observed == pane_label(Pane.ACCESSIBILITY)
    assert clock.sleeps == [0.25, 0.25]


# --- cli.py (LaunchServices oracle, remaining verdict arms, clean/remove) ------


def _combined(result: object) -> str:
    """Return stdout plus stderr, whichever stream the click version splits out."""
    return str(getattr(result, "output", "")) + str(getattr(result, "stderr", "") or "")


@pytest.fixture
def cli_no_actionable_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> BundleIndex:
    """Stub inputs whose only row is live, so the CLI finds nothing to clean."""
    index = make_index({"com.example.live": "Live App"}, tmp_path)
    snapshot = TccSnapshot(
        entries=[make_entry("com.example.live", kind=ClientKind.BUNDLE_ID)],
        unrecognized=[],
    )
    _patch_cli_inputs(
        monkeypatch,
        snapshot=snapshot,
        index=index,
        registered=lambda _bundle_id: True,
    )
    return index


def test_registered_bundle_ids_matches_whole_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dump = (
        "    identifier:                 com.example.live\n"
        "    identifier:                 com.example.live.helper.Renderer\n"
        "    path:                       /Applications/Live.app\n"
    )
    invoked: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        invoked.append(argv)
        return SimpleNamespace(stdout=dump)

    monkeypatch.setattr(cli.subprocess, "run", run)
    cli._registered_bundle_ids.cache_clear()
    try:
        assert cli._registered_bundle_ids() == frozenset(
            {"com.example.live", "com.example.live.helper.Renderer"},
        )
        assert cli._registered("com.example.live.helper") is False
        assert cli._registered("com.example.live") is True
        assert invoked == [[cli._LSREGISTER, "-dump"]]
    finally:
        cli._registered_bundle_ids.cache_clear()


def test_cli_audit_json_lists_every_verdict_class(
    cli_full_env: BundleIndex,
) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["audit", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    statuses = {row["status"] for row in payload["rows"]}
    assert {
        "dead-path",
        "hidden-orphan",
        "name-ambiguous",
        "policy-kept",
        "unrecognized",
    } <= statuses
    assert payload["actionable"] == 2


def test_cli_audit_omits_the_actionable_hint_when_nothing_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    index = make_index({"com.example.live": "Live App"}, tmp_path)
    snapshot = TccSnapshot(
        entries=[make_entry("com.example.live", kind=ClientKind.BUNDLE_ID)],
        unrecognized=[],
    )
    _patch_cli_inputs(
        monkeypatch,
        snapshot=snapshot,
        index=index,
        registered=lambda _bundle_id: True,
    )

    result = CliRunner().invoke(cli.app, ["audit"])
    assert result.exit_code == 0
    assert "actionable row(s)" not in _combined(result)


def test_cli_clean_reports_a_clean_pool(cli_no_actionable_env: BundleIndex) -> None:
    result = CliRunner().invoke(cli.app, ["clean"])
    assert result.exit_code == 0
    assert "No actionable rows - the lists are clean." in _combined(result)


def test_cli_clean_reports_a_clean_pool_as_json(
    cli_no_actionable_env: BundleIndex,
) -> None:
    result = CliRunner().invoke(cli.app, ["clean", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"results": [], "failed": 0, "skipped": 0}


def test_cli_audit_reports_a_verdict_the_renderer_does_not_know(
    monkeypatch: pytest.MonkeyPatch,
    cli_audit_env: BundleIndex,
) -> None:
    """A verdict class added upstream is reported, never silently dropped.

    The `match` in `audit_command` carries an explicit wildcard case, so an
    unknown verdict renders as `unknown-verdict` rather than vanishing from a
    report the user reads as complete.
    """
    future = SimpleNamespace(name="future-verdict")
    real_classify = cli.Auditor.classify

    def classify(
        self: object,
        entries: list[TCCEntry],
        unrecognized: list[UnrecognizedEntry] | None = None,
    ) -> list[object]:
        return [*real_classify(self, entries, unrecognized or []), future]

    monkeypatch.setattr(cli.Auditor, "classify", classify)

    result = CliRunner().invoke(cli.app, ["audit", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    statuses = {row["status"] for row in payload["rows"]}
    assert "dead-path" in statuses
    unknown = [row for row in payload["rows"] if row["status"] == "unknown-verdict"]
    assert len(unknown) == 1
    assert unknown[0]["client"] == "SimpleNamespace"
    assert "upgrade tcctool" in unknown[0]["reason"]


def test_cli_clean_reuses_one_process_per_pane(
    monkeypatch: pytest.MonkeyPatch,
    cli_full_env: BundleIndex,
) -> None:
    process = _FakeProcess([_row("dead-tool"), _row("second-tool")])
    attaches: list[int] = []

    def attach() -> object:
        attaches.append(1)
        return process

    monkeypatch.setattr(cli, "SystemSettingsProcess", SimpleNamespace(attach=attach))
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)

    result = CliRunner().invoke(cli.app, ["clean", "--json", "--dry-run"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["results"] == [
        {
            "pane": "Privacy_ScreenCapture",
            "name": "dead-tool",
            "outcome": "Selection",
            "handled": True,
        },
        {
            "pane": "Privacy_ScreenCapture",
            "name": "second-tool",
            "outcome": "Selection",
            "handled": True,
        },
    ]
    assert payload["skipped"] == 3
    assert payload["failed"] == 0
    assert len(attaches) == 1


def test_cli_clean_fails_on_actionable_rows_without_a_pane(
    monkeypatch: pytest.MonkeyPatch,
    cli_full_env: BundleIndex,
) -> None:
    """A row `audit` called actionable but `clean` cannot route is a failure."""
    monkeypatch.delitem(cli.PANE_BY_SERVICE, Service.SCREEN_RECORDING)

    def unexpected_ax(_pane: Pane) -> bool:
        msg = "clean must not reach the AX path when the pane mapping is absent"
        raise AssertionError(msg)

    monkeypatch.setattr(cli.settings_module, "ensure_running", unexpected_ax)

    result = CliRunner().invoke(cli.app, ["clean", "--json", "--dry-run"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["results"] == [
        {
            "pane": "unknown",
            "name": "dead-tool",
            "outcome": "skipped (no pane mapping)",
            "handled": False,
        },
        {
            "pane": "unknown",
            "name": "second-tool",
            "outcome": "skipped (no pane mapping)",
            "handled": False,
        },
    ]
    assert payload["failed"] == 2


def test_cli_clean_reports_when_settings_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
    cli_full_env: BundleIndex,
) -> None:
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: False)

    result = CliRunner().invoke(cli.app, ["clean", "--json", "--dry-run"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert [row["outcome"] for row in payload["results"]] == [
        "failed (System Settings did not start)",
        "failed (System Settings did not start)",
    ]
    assert payload["failed"] == 2


def test_cli_clean_reports_when_the_ax_tree_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    cli_full_env: BundleIndex,
) -> None:
    monkeypatch.setattr(
        cli, "SystemSettingsProcess", SimpleNamespace(attach=lambda: None)
    )
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)

    result = CliRunner().invoke(cli.app, ["clean", "--json", "--dry-run"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert [row["outcome"] for row in payload["results"]] == [
        "failed (System Settings not addressable via AX)",
        "failed (System Settings not addressable via AX)",
    ]
    assert payload["failed"] == 2


def test_cli_clean_fails_on_a_wrong_pane(
    monkeypatch: pytest.MonkeyPatch,
    cli_audit_env: BundleIndex,
) -> None:
    """A pane other than the requested one must fail closed and remove nothing."""
    monkeypatch.setattr(settings, "time", _Clock())
    process = _FakeProcess([_row("dead-tool")], displays=Pane.ACCESSIBILITY)
    monkeypatch.setattr(
        cli,
        "SystemSettingsProcess",
        SimpleNamespace(attach=lambda: process),
    )
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)

    result = CliRunner().invoke(cli.app, ["clean", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["results"] == [
        {
            "pane": "Privacy_ScreenCapture",
            "name": "dead-tool",
            "outcome": "WrongPane",
            "handled": False,
        },
    ]
    assert payload["failed"] == 1
    assert process.selected == []
    assert process.pressed == []
    output = _combined(result)
    assert "Privacy_ScreenCapture" in output
    assert pane_label(Pane.ACCESSIBILITY) in output


def test_cli_clean_reports_a_row_that_survived(
    monkeypatch: pytest.MonkeyPatch,
    cli_audit_env: BundleIndex,
) -> None:
    monkeypatch.setattr(
        cli,
        "SystemSettingsProcess",
        SimpleNamespace(attach=lambda: _FakeProcess([_row("dead-tool")])),
    )
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)
    monkeypatch.setattr(
        cli.removal_module,
        "remove_and_verify",
        lambda *_args, **_kwargs: removal.StillPresent(
            pane=Pane.SCREEN_RECORDING,
            name="dead-tool",
        ),
    )

    result = CliRunner().invoke(cli.app, ["clean", "--json"])
    assert result.exit_code == 1
    assert "still listed" in _combined(result)


def test_cli_clean_prints_plain_results_and_counts_skips(
    monkeypatch: pytest.MonkeyPatch,
    cli_full_env: BundleIndex,
) -> None:
    process = _FakeProcess([_row("dead-tool"), _row("second-tool")])
    monkeypatch.setattr(
        cli,
        "SystemSettingsProcess",
        SimpleNamespace(attach=lambda: process),
    )
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)

    result = CliRunner().invoke(cli.app, ["clean", "--dry-run"])
    assert result.exit_code == 0
    assert "dead-tool" in _combined(result)
    assert "Selection" in _combined(result)
    assert "3 inert or ambiguous row(s) skipped" in _combined(result)


def test_cli_clean_omits_the_skip_summary_when_nothing_was_skipped(
    monkeypatch: pytest.MonkeyPatch,
    cli_audit_env: BundleIndex,
) -> None:
    process = _FakeProcess([_row("dead-tool")])
    monkeypatch.setattr(
        cli,
        "SystemSettingsProcess",
        SimpleNamespace(attach=lambda: process),
    )
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)

    result = CliRunner().invoke(cli.app, ["clean", "--dry-run"])
    assert result.exit_code == 0
    assert "Selection" in _combined(result)
    assert "inert or ambiguous" not in _combined(result)


def test_cli_clean_prints_a_failure_summary_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    cli_audit_env: BundleIndex,
) -> None:
    monkeypatch.setattr(
        cli,
        "SystemSettingsProcess",
        SimpleNamespace(attach=lambda: None),
    )
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)

    result = CliRunner().invoke(cli.app, ["clean"])
    assert result.exit_code == 1
    output = _combined(result)
    assert "failed (System Settings not addressable via AX)" in output
    assert "1 of 1 row(s) did not complete" in output


def test_cli_remove_requires_settings_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: False)

    result = CliRunner().invoke(
        cli.app,
        ["remove", "Privacy_ScreenCapture", "Screen Tool"],
    )
    assert result.exit_code == 1
    assert "System Settings did not start." in _combined(result)


def test_cli_remove_requires_the_ax_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)
    monkeypatch.setattr(
        cli, "SystemSettingsProcess", SimpleNamespace(attach=lambda: None)
    )

    result = CliRunner().invoke(
        cli.app,
        ["remove", "Privacy_ScreenCapture", "Screen Tool"],
    )
    assert result.exit_code == 1
    assert "not addressable via AX" in _combined(result)


@pytest.mark.parametrize(
    ("outcome", "expected_code", "expected_text"),
    [
        (
            removal.Removed(pane=Pane.SCREEN_RECORDING, name="Screen Tool"),
            0,
            "verified gone",
        ),
        (
            removal.StillPresent(pane=Pane.SCREEN_RECORDING, name="Screen Tool"),
            1,
            "still listed",
        ),
        (
            removal.NotFound(pane=Pane.SCREEN_RECORDING, name="Screen Tool"),
            1,
            "No row named",
        ),
        (
            removal.WrongPane(
                pane=Pane.SCREEN_RECORDING,
                observed=pane_label(Pane.ACCESSIBILITY),
            ),
            1,
            "Wrong pane",
        ),
        (
            removal.Refused(
                pane=Pane.SCREEN_RECORDING, name="Screen Tool", occurrences=2
            ),
            1,
            "Refused: 2 rows",
        ),
        (
            removal.SelectFailed(pane=Pane.SCREEN_RECORDING, name="Screen Tool"),
            1,
            "could not be selected",
        ),
        (
            removal.RemoveButtonMissing(pane=Pane.SCREEN_RECORDING, name="Screen Tool"),
            1,
            "Removal did not complete",
        ),
    ],
    ids=[
        "removed",
        "still-present",
        "not-found",
        "wrong-pane",
        "refused",
        "select-failed",
        "other-failure",
    ],
)
def test_cli_remove_maps_each_outcome_to_an_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    outcome: removal.RemovalOutcome,
    expected_code: int,
    expected_text: str,
) -> None:
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)
    monkeypatch.setattr(
        cli, "SystemSettingsProcess", SimpleNamespace(attach=lambda: object())
    )
    monkeypatch.setattr(
        cli.removal_module,
        "remove_and_verify",
        lambda *_args, **_kwargs: outcome,
    )

    result = CliRunner().invoke(
        cli.app,
        ["remove", "Privacy_ScreenCapture", "Screen Tool"],
    )
    assert result.exit_code == expected_code
    assert expected_text in _combined(result)


def test_cli_remove_names_the_requested_and_observed_panes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.settings_module, "ensure_running", lambda _pane: True)
    monkeypatch.setattr(
        cli, "SystemSettingsProcess", SimpleNamespace(attach=lambda: object())
    )
    monkeypatch.setattr(
        cli.removal_module,
        "remove_and_verify",
        lambda *_args, **_kwargs: removal.WrongPane(
            pane=Pane.SCREEN_RECORDING,
            observed=pane_label(Pane.ACCESSIBILITY),
        ),
    )

    result = CliRunner().invoke(
        cli.app,
        ["remove", "Privacy_ScreenCapture", "Screen Tool"],
    )

    assert result.exit_code == 1
    output = _combined(result)
    assert "Privacy_ScreenCapture" in output
    assert pane_label(Pane.SCREEN_RECORDING) in output
    assert pane_label(Pane.ACCESSIBILITY) in output


@pytest.mark.parametrize("command", [["audit"], ["clean"]])
def test_cli_reports_an_unreadable_tcc_database_as_a_tool_failure(
    monkeypatch: pytest.MonkeyPatch,
    cli_audit_env: BundleIndex,
    command: list[str],
) -> None:
    """A gated TCC database is a classified failure, never a driver traceback.

    The database looks world-readable but its open needs Full Disk Access, so
    this is the expected failure on a fresh terminal; the CLI must name it and
    exit distinguishably instead of printing a sqlite traceback.
    """

    def unreadable(*_args: object, **_kwargs: object) -> TccSnapshot:
        raise sqlite3.OperationalError("authorization denied")

    monkeypatch.setattr(cli, "read_snapshot", unreadable)
    result = CliRunner().invoke(cli.app, command)
    assert result.exit_code == 2
    assert "Full Disk Access" in result.stderr
    assert "Traceback" not in _combined(result)
    assert result.stdout == ""

import json
import os

from history import Audit, History, revision_of


def test_a_revision_depends_on_content_only():
    a = {"x": 1, "y": [1, 2], "z": {"b": 1, "a": 2}}
    b = {"z": {"a": 2, "b": 1}, "y": [1, 2], "x": 1}
    assert revision_of(a) == revision_of(b) and len(revision_of(a)) == 12
    assert revision_of(a) != revision_of({**a, "x": 2})
    assert revision_of({"name": "ü"}) == revision_of(json.loads('{"name": "\\u00fc"}'))


def test_versions_are_saved_listed_newest_first_and_fetched(tmp_path):
    clock = iter(range(1000, 2000))
    h = History(str(tmp_path), clock=lambda: next(clock))
    r1 = h.save({"v": 1}, "ui", "first")
    r2 = h.save({"v": 2}, "api:agent", "second")
    assert [r["revision"] for r in h.list()] == [r2["revision"], r1["revision"]]
    assert h.list()[0]["source"] == "api:agent" and h.list()[0]["note"] == "second"
    assert h.get(r1["revision"])["settings"] == {"v": 1} and h.get("nope") is None


def test_saving_the_same_content_twice_in_a_row_keeps_one_version(tmp_path):
    h = History(str(tmp_path))
    assert h.save({"v": 1}, "ui") is not None
    assert h.save({"v": 1}, "ui") is None
    assert h.save({"v": 2}, "ui") is not None
    assert h.save({"v": 1}, "ui") is not None            # going back is a new step, not a duplicate
    assert len(h.list()) == 3


def test_only_the_newest_versions_are_kept(tmp_path):
    h = History(str(tmp_path), keep=3)
    for i in range(6):
        h.save({"v": i}, "ui")
    assert [h.get(r["revision"])["settings"]["v"] for r in h.list()] == [5, 4, 3]


def test_a_damaged_version_is_skipped_not_fatal(tmp_path):
    h = History(str(tmp_path))
    h.save({"v": 1}, "ui")
    h.save({"v": 2}, "ui")
    newest = sorted(os.listdir(h.dir))[-1]
    with open(os.path.join(h.dir, newest), "w") as fh:
        fh.write("{ broken")
    assert len(h.list()) == 1
    assert History(str(tmp_path / "missing")).list() == []


def test_the_audit_log_appends_rotates_and_reads_back_newest_first(tmp_path):
    a = Audit(str(tmp_path / "audit.jsonl"), max_bytes=400)
    for i in range(30):
        a.record(token="agent", method="GET", path=f"/v1/leases/{i}", status=200)
    entries = a.tail(5)
    assert [e["path"] for e in entries] == [f"/v1/leases/{i}" for i in (29, 28, 27, 26, 25)]
    assert os.path.exists(str(tmp_path / "audit.jsonl") + ".1")        # rotated
    assert all(set(e) == {"time", "token", "method", "path", "status"} for e in entries)


def test_audit_survives_an_unwritable_place(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    Audit(str(blocked / "audit.jsonl")).record(token="t")             # no exception
    assert Audit(str(tmp_path / "none.jsonl")).tail() == []

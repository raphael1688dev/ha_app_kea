import pytest

from proposals import EXPIRE_SECONDS, MAX_PROPOSALS, Proposals, ProposalError


@pytest.fixture
def box(tmp_path):
    clock = {"t": 1_000_000}
    p = Proposals(str(tmp_path / "proposals.json"), clock=lambda: clock["t"])
    p.tick = lambda s: clock.update(t=clock["t"] + s)
    return p


def make(box, note="add the printer", base="rev1"):
    return box.create("agent", {"v": 1}, base, ["dhcp4 192.168.1.0/24: reservation added"], note)


def test_a_proposal_starts_pending_and_is_listed_without_its_settings(box):
    made = make(box)
    assert made["status"] == "pending" and made["token"] == "agent" and made["changes"]
    assert "settings" not in made and "settings" not in box.list()[0]
    assert box.get(made["id"], with_settings=True)["settings"] == {"v": 1}


def test_approval_needs_the_same_revision_the_proposal_was_made_against(box):
    made = make(box)
    with pytest.raises(ProposalError) as stale:
        box.decide(made["id"], "applied", current_revision="other")
    assert stale.value.code == "stale" and box.get(made["id"])["status"] == "pending"
    assert box.is_stale(made["id"], "other") and not box.is_stale(made["id"], "rev1")
    done = box.decide(made["id"], "applied", "applied as revision abc", current_revision="rev1")
    assert done["status"] == "applied" and done["result"].startswith("applied")


def test_a_decision_cannot_be_taken_twice(box):
    made = make(box)
    box.decide(made["id"], "rejected", "not now")
    with pytest.raises(ProposalError) as again:
        box.decide(made["id"], "rejected")
    assert again.value.code == "not_pending"
    with pytest.raises(ProposalError) as missing:
        box.decide("nope", "rejected")
    assert missing.value.code == "not_found"
    with pytest.raises(ProposalError):
        box.get("nope")


def test_a_pending_proposal_expires(box):
    made = make(box)
    box.tick(EXPIRE_SECONDS + 1)
    expired = box.get(made["id"])
    assert expired["status"] == "rejected" and "expired" in expired["result"]
    assert box.list("pending") == []


def test_proposals_survive_a_restart_and_the_list_is_bounded(box, tmp_path):
    first = make(box)
    assert Proposals(box.path).get(first["id"])["note"] == "add the printer"
    for i in range(MAX_PROPOSALS + 5):
        made = make(box, note=f"n{i}")
        box.decide(made["id"], "rejected")
    assert len(box.list()) <= MAX_PROPOSALS + 1
    assert box.get(first["id"])["status"] == "pending"                   # pending ones are never dropped


def test_filtering_by_status_and_newest_first(box):
    a, b = make(box, "a"), make(box, "b")
    box.decide(a["id"], "rejected")
    assert [p["note"] for p in box.list()] == ["b", "a"] and [p["note"] for p in box.list("pending")] == ["b"]
    assert [p["note"] for p in box.list("rejected")] == ["a"]

from stratus.jobs.audit_gate import due_checkpoint


def test_gate_checkpoint_schedule() -> None:
    assert due_checkpoint(29, set()) is None
    assert due_checkpoint(30, set()) == 30
    assert due_checkpoint(60, set()) == 30
    assert due_checkpoint(60, {30}) == 60
    assert due_checkpoint(90, {30, 60}) is None

from baritone_client import observability


def test_event_journal_rotates_before_exceeding_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(observability, "_EVENT_MAX_BYTES", 400)
    monkeypatch.setattr(observability, "_EVENT_BACKUPS", 2)

    for sequence in range(8):
        observability.emit_event("rotation_probe", sequence=sequence, detail="x" * 80)

    journal = tmp_path / "telemetry" / "events.jsonl"
    first_backup = tmp_path / "telemetry" / "events.jsonl.1"
    assert journal.stat().st_size <= 400
    assert first_backup.exists()
    assert first_backup.stat().st_size <= 400


def test_navigation_can_finish_with_explicit_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, "_OUTPUT_DIR", tmp_path)
    observability.observe_command_response(
        "goto", {"x": 1, "y": 64, "z": 2}, {"started": True}
    )
    assert observability.finish_navigation(
        "arrived", {"position": {"x": 1, "y": 64, "z": 2}}
    )
    lines = (tmp_path / "telemetry" / "events.jsonl").read_text().splitlines()
    assert any('"event":"navigation_ended"' in line and '"outcome":"arrived"' in line for line in lines)

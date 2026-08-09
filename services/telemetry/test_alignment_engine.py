import pytest

from services.telemetry.alignment_engine import AlignmentEngine


@pytest.fixture
def mock_files(tmp_path):
    manifesto = tmp_path / "manifesto.md"
    watchdog_log = tmp_path / "watchdog.log"

    # Write a manifesto with enough content to pass the >100 char check
    manifesto.write_text(
        "# HORI Manifesto\n\n"
        "This is a test manifesto with enough content to pass the "
        "intent integrity check. It needs to be longer than 100 "
        "characters so the alignment engine considers it present."
    )
    watchdog_log.write_text("")

    return manifesto, watchdog_log

def test_alignment_engine_full_alignment(mock_files):
    manifesto, watchdog_log = mock_files
    engine = AlignmentEngine(manifesto, watchdog_log)

    report = engine.get_report()

    assert report["alignment_score"] == 1.0
    assert report["status"] == "Aligned"
    assert report["manifesto_present"] is True

def test_alignment_engine_drift(mock_files):
    manifesto, watchdog_log = mock_files

    # Add failures to watchdog log (using "DOWN" to trigger stability failure)
    watchdog_log.write_text(
        "🚨 [WATCHDOG] CRITICAL: Service 'test_service' is DOWN\n"
        "🚨 [WATCHDOG] CRITICAL: Service 'other_service' is DOWN\n"
        "🚨 [WATCHDOG] CRITICAL: Service 'third_service' is DOWN\n"
    )

    engine = AlignmentEngine(manifesto, watchdog_log)
    report = engine.get_report()

    # Intent integrity: 1.0 (manifesto present and >100 chars)
    # Stability: 0.3 (failures >= 3)
    # Governance: 1.0 (watchdog log exists and was updated)
    # Composite: (1.0 + 0.3 + 1.0) / 3 = 0.7666...

    assert report["alignment_score"] == pytest.approx(0.77, abs=0.01)
    assert report["status"] == "Drifting"

def test_alignment_engine_critical_drift(mock_files):
    manifesto, watchdog_log = mock_files

    # Many failures
    watchdog_log.write_text("🚨 [WATCHDOG] CRITICAL: Service '1' is DOWN\n" * 5)

    # Make manifesto empty to fail intent integrity
    manifesto.write_text("")
    engine = AlignmentEngine(manifesto, watchdog_log)
    report = engine.get_report()

    # Intent: 0.0 (manifesto empty)
    # Stability: 0.3
    # Governance: 1.0
    # Composite: (0.0 + 0.3 + 1.0) / 3 = 0.4333...

    assert report["alignment_score"] == 0.43
    assert report["status"] == "Critical Drift"

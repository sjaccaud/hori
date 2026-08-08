import pytest
import json
import os
from pathlib import Path
from services.telemetry.alignment_engine import AlignmentEngine

@pytest.fixture
def mock_files(tmp_path):
    manifesto = tmp_path / "manifesto.json"
    charter = tmp_path / "charter.json"
    watchdog_log = tmp_path / "watchdog.log"

    manifesto.write_text(json.dumps({"id": "m1", "name": "AIOS Manifesto"}))
    charter.write_text(json.dumps({"id": "c1", "parent_manifesto_id": "m1", "name": "AIOS Charter"}))
    watchdog_log.write_text("")

    return manifesto, charter, watchdog_log

def test_alignment_engine_full_alignment(mock_files):
    manifesto, charter, watchdog_log = mock_files
    engine = AlignmentEngine(manifesto, charter, watchdog_log)
    
    report = engine.get_report()
    
    assert report["alignment_score"] == 1.0
    assert report["status"] == "Aligned"
    assert report["manifesto_id"] == "m1"
    assert report["charter_id"] == "c1"

def test_alignment_engine_drift(mock_files):
    manifesto, charter, watchdog_log = mock_files
    
    # Break link between manifesto and charter
    charter.write_text(json.dumps({"id": "c1", "parent_manifesto_id": "wrong", "name": "AIOS Charter"}))
    
    # Add failures to watchdog log (using "DOWN" to trigger stability failure)
    # Use a single write_text to ensure all lines are present
    watchdog_log.write_text(
        "🚨 [WATCHDOG] CRITICAL: Service 'test_service' is DOWN\n"
        "🚨 [WATCHDOG] CRITICAL: Service 'other_service' is DOWN\n"
        "🚨 [WATCHDOG] CRITICAL: Service 'third_service' is DOWN\n"
    )

    engine = AlignmentEngine(manifesto, charter, watchdog_log)
    report = engine.get_report()
    
    # Intent integrity: 0.5 (manifesto/charter exist) + 0.0 (not linked) = 0.5
    # Stability: 0.3 (failures >= 3)
    # Governance: 1.0 (watchdog log exists and was updated)
    # Composite (simple average): (0.5 + 0.3 + 1.0) / 3 = 1.8 / 3 = 0.6
    
    assert report["alignment_score"] == pytest.approx(0.6)
    assert report["status"] == "Drifting"

def test_alignment_engine_critical_drift(mock_files):
    manifesto, charter, watchdog_log = mock_files
    
    # Break link
    charter.write_text(json.dumps({"id": "c1", "parent_manifesto_id": "wrong", "name": "AIOS Charter"}))
    
    # Many failures (using "DOWN" to trigger stability failure)
    watchdog_log.write_text("🚨 [WATCHDOG] CRITICAL: Service '1' is DOWN\n" * 5)
    
    # Governance: watchdog log exists but maybe old? 
    # For simplicity, we'll just assume it's new.
    
    engine = AlignmentEngine(manifesto, charter, watchdog_log)
    report = engine.get_report()
    
    # Intent: 0.5
    # Stability: 0.3
    # Governance: 1.0
    # Composite (simple average): (0.5 + 0.3 + 1.0) / 3 = 0.6
    
    # Let's force a lower score by making manifesto empty
    manifesto.write_text("{}")
    engine = AlignmentEngine(manifesto, charter, watchdog_log)
    report = engine.get_report()
    # Intent: 0.0
    # Stability: 0.3
    # Governance: 1.0
    # Composite (simple average): (0.0 + 0.3 + 1.0) / 3 = 1.3 / 3 = 0.4333...
    # Note: AlignmentEngine.get_report() rounds the score to 2 decimal places.
    
    assert report["alignment_score"] == 0.43
    assert report["status"] == "Critical Drift"

"""Anomaly Detection Agent: flags statistically unusual inventory & sales events."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from backend import anomaly_detection as anom_backend
from config import ANOMALY_DISCLAIMER

DbPath = Optional[Union[str, Path]]


class AnomalyAgent:
    """Specialized agent responsible for detecting unusual inventory events."""

    name = "Anomaly Detection Agent"

    def __init__(self, db_path: DbPath = None) -> None:
        self.db_path = db_path

    def scan_anomalies(self) -> dict:
        detailed = anom_backend.run_anomaly_scan_detailed(db_path=self.db_path)
        anomalies = detailed["anomalies"]
        severity_counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
        for a in anomalies:
            sev = a.get("severity", "Low")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        return {
            "agent": self.name,
            "total": len(anomalies),
            "severity_counts": severity_counts,
            "anomalies": anomalies,
            "checks_executed": detailed["checks_executed"],
            "checks_skipped": detailed["checks_skipped"],
            "disclaimer": ANOMALY_DISCLAIMER,
        }

    def get_anomalies_for_query(self, text_query: str = "") -> list[dict]:
        all_anoms = anom_backend.detect_anomalies(db_path=self.db_path)
        q = (text_query or "").strip().lower()
        if not q:
            return all_anoms
        matched = [
            a for a in all_anoms
            if q in a["product_name"].lower()
            or q in a["product_id"].lower()
            or q in a["detected_event"].lower()
        ]
        return matched if matched else all_anoms

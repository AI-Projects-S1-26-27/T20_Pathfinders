import json
import os
from typing import Any, Dict

from src.sim.interfaces import MetricsLogger


class SimulationLogger(MetricsLogger):
    """Writes one JSON line per event; schema in docs/log_schema.md."""

    def __init__(
        self,
        log_path: str,
        run_id: str,
        mode: str,
        seed: int,
        scenario: str,
        rate: float,
        file_mode: str = "w",
    ):
        self.log_path = log_path
        self.run_id = run_id
        self.mode = mode
        self.seed = seed
        self.scenario = scenario
        self.rate = rate
        dirname = os.path.dirname(self.log_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        # "w" truncates any old log so each run starts clean.
        with open(self.log_path, file_mode, encoding="utf-8"):
            pass
        # Kept open between rows (W5 profiling: reopening per row cost ~3 s
        # per 20-vehicle run); every row is flushed, so a crash keeps them all.
        self._file = None

    def log(
        self,
        event_type: str,
        timestamp: float,
        payload: Dict[str, Any],
        agent_id: str = "env",
    ) -> None:
        record = {
            "run_id": self.run_id,
            "mode": self.mode,
            "seed": self.seed,
            "scenario": self.scenario,
            "rate": self.rate,
            "t": round(float(timestamp), 2),
            "agent_id": agent_id,
            "event": event_type,
            "payload": payload,
        }
        if self._file is None:
            self._file = open(self.log_path, "a", encoding="utf-8")
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Release the file (needed on Windows before renaming it). Logging
        again afterwards reopens it in append mode."""
        if self._file is not None:
            self._file.close()
            self._file = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

"""Human-readable live log: one flushed line per event, for `tail -f runs/<id>/run.log`.

audit.jsonl stays the structured record; this is only for following a run as it happens.
Callers pass explicit fields, so nothing outside those fields (environment, keys) can leak.
"""
import sys
from pathlib import Path

RUN_LOG_FILENAME = "run.log"


class RunLog:
    def __init__(self, streams, clock, owned=()):
        self._streams, self._clock, self._owned = list(streams), clock, list(owned)

    @classmethod
    def for_run(cls, run_dir, clock, stderr=sys.stderr):
        """Mirror to stderr and to a fresh <run dir>/run.log (a run dir is never reused)."""
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        file = open(Path(run_dir) / RUN_LOG_FILENAME, "w", encoding="utf-8")
        return cls([stderr, file], clock, owned=[file])

    def event(self, name, **fields):
        details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        line = f"{self._clock().strftime('%H:%M:%S')} {name:<12} {details}".rstrip()
        for stream in self._streams:
            stream.write(line + "\n")
            stream.flush()

    def close(self):
        for stream in self._owned:
            stream.close()


class NullRunLog:
    def event(self, name, **fields):
        pass

    def close(self):
        pass

"""Reads manifest.yaml, the single source of truth for each agent's model and tools."""
from dataclasses import dataclass
from pathlib import Path

import yaml

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.yaml"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    model: str
    tools: tuple
    workflow: str
    reviews: tuple = ()


def load_agent_specs(path=MANIFEST_PATH):
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["agents"]
    return {
        name: AgentSpec(
            name=name,
            model=entry["model"],
            tools=tuple(entry["tools"]),
            workflow=entry["workflow"],
            reviews=tuple(entry.get("reviews", ())),
        )
        for name, entry in raw.items()
    }

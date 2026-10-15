"""The README's criteria-to-evidence map must only point at files that exist, so it cannot rot."""
import re

import pytest

from harness.tools import PROJECT_ROOT

README = PROJECT_ROOT / "README.md"
MAP_HEADING = "Criteria-to-evidence map"
BACKTICKED = re.compile(r"`([^`\s]+)`")


def evidence_map_section(text):
    start = text.index(MAP_HEADING)
    end = text.find("\n## ", start + len(MAP_HEADING))
    return text[start:end if end != -1 else len(text)]


def listed_paths(section):
    """Backticked tokens in the map that look like repo paths (they contain a slash)."""
    return sorted({token for token in BACKTICKED.findall(section) if "/" in token})


def test_listed_paths_picks_only_backticked_tokens_with_a_slash():
    section = "| a | uses `launch_*` and `harness/run.py` | `results/x.json`, `tests/` |"
    assert listed_paths(section) == ["harness/run.py", "results/x.json", "tests/"]


def test_the_map_section_stops_at_the_next_heading():
    text = f"intro\n## 3. {MAP_HEADING}\n`a/b`\n## Results\n`c/d`\n"
    assert listed_paths(evidence_map_section(text)) == ["a/b"]


def test_the_readme_has_an_evidence_map_with_paths():
    assert len(listed_paths(evidence_map_section(README.read_text(encoding="utf-8")))) >= 20


@pytest.mark.parametrize("path", listed_paths(evidence_map_section(README.read_text(encoding="utf-8"))))
def test_every_evidence_path_in_the_readme_exists(path):
    assert (PROJECT_ROOT / path).exists(), f"README evidence map lists {path}, which does not exist"

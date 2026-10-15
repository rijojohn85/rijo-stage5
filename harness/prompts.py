"""System prompts live in prompts/<workflow>.md."""
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class PromptLibrary:
    def __init__(self, directory=PROMPTS_DIR):
        self._dir = Path(directory)

    def system_prompt(self, workflow):
        return (self._dir / f"{workflow}.md").read_text(encoding="utf-8")

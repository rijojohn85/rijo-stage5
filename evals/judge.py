"""LLM judge for the two writer criteria that need judgment (tone, no-new-findings).

The rubric is a fixed text file under evals/rubrics/, the model is the coordinator-grade model
from manifest.yaml, and the call goes through the same Fireworks client the runtime uses (no
CLI). `llm` is injected so tests run it with a fake. The judge's verdict is a model judgment,
which README states as a limit; it is not ground truth.
"""
import json
from pathlib import Path

RUBRIC_DIR = Path(__file__).resolve().parent / "rubrics"


class Judge:
    def __init__(self, llm, model):
        self._llm, self._model = llm, model

    def problems(self, rubric_name, material):
        """[] when the rubric passes, otherwise one problem string (including an unusable judge reply)."""
        rubric = (RUBRIC_DIR / f"{rubric_name}.md").read_text(encoding="utf-8")
        messages = [{"role": "system", "content": rubric},
                    {"role": "user", "content": json.dumps(material, indent=2)}]
        verdict = _parse(self._llm.chat(self._model, messages, []).content)
        if verdict is None:
            return [f"{rubric_name}: the judge did not return the required JSON verdict"]
        return [] if verdict["pass"] else [f"{rubric_name}: {verdict.get('reason', 'no reason given')}"]


def _parse(text):
    """The verdict object, or None. Tolerates text around the object: the judge is not a contract under test."""
    text = text or ""
    start, end = text.find("{"), text.rfind("}")
    try:
        verdict = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return verdict if isinstance(verdict, dict) and isinstance(verdict.get("pass"), bool) else None


def default_judge():
    from harness.llm import FireworksClient
    from harness.manifest import load_coordinator_spec
    return Judge(FireworksClient.from_env(), load_coordinator_spec().model)

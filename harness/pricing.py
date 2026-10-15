"""Cost is calculated from token usage and config/prices.json; it is not a measured bill."""
import json
from pathlib import Path

PRICES_PATH = Path(__file__).resolve().parent.parent / "config" / "prices.json"
TOKENS_PER_PRICE_UNIT = 1_000_000  # prices.json is USD per 1M tokens
COST_DECIMALS = 6


class PriceTable:
    def __init__(self, models):
        self._models = models

    @classmethod
    def from_file(cls, path=PRICES_PATH):
        return cls(json.loads(Path(path).read_text(encoding="utf-8"))["models"])

    def cost_usd(self, model, usage):
        """Price input and output tokens only; cached-input discounts are ignored (upper bound)."""
        price = self._models[model]
        total = (usage.input_tokens * price["input"] + usage.output_tokens * price["output"])
        return round(total / TOKENS_PER_PRICE_UNIT, COST_DECIMALS)

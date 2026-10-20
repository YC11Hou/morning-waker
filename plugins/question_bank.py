"""Gate plugin: one question a day from a TOML bank; the answer passes when it covers every key idea."""
from __future__ import annotations

import random
import tomllib
from datetime import date as Date
from pathlib import Path

from morningwaker.config import ROOT
from morningwaker.gate import Check, Context


class QuestionBank(Check):
    title = "Question"

    def __init__(self, name: str, options: dict):
        super().__init__(name, options)
        path = Path(options.get("bank", ""))
        path = path if path.is_absolute() else ROOT / path
        with open(path, "rb") as f:
            self.questions = tomllib.load(f).get("question", [])
        if not self.questions:
            raise ValueError(f"question bank {path} has no [[question]] entries")
        self.min_chars = int(options.get("min_chars", 60))

    def today(self, ctx: Context) -> dict:
        # A fixed shuffle walked one step per day, so no question repeats until the bank is exhausted.
        order = list(range(len(self.questions)))
        random.Random(len(order)).shuffle(order)
        return self.questions[order[Date.fromisoformat(ctx.date).toordinal() % len(order)]]

    def prompt(self, ctx: Context) -> dict:
        q = self.today(ctx)
        return {"text": f"{q['title']}\n\n{q['text'].strip()}\n\nWrite your approach and its time complexity.",
                "input": True, "button": "Submit answer"}

    def verify(self, ctx: Context, answer: str) -> tuple[bool, str]:
        q, text = self.today(ctx), answer.lower()
        if len(answer.strip()) < self.min_chars:
            return False, f"Too short: write at least {self.min_chars} characters."
        ideas = q.get("ideas", [])
        missing = [group for group in ideas if not any(alias.lower() in text for alias in group)]
        # One missing idea is tolerated: the gate checks that you are awake and thinking, not exact wording.
        if len(missing) > int(self.options.get("allowed_missing", 1)):
            hints = ", ".join("time complexity" if any("o(" in a for a in g) else g[0] for g in missing)
            return False, f"Covers {len(ideas) - len(missing)} of {len(ideas)} key ideas. Missing: {hints}."
        return True, "Passed. Reference: " + q.get("reference", "").strip()


def make(name: str, options: dict) -> Check:
    return QuestionBank(name, options)

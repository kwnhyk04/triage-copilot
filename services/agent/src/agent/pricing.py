"""What Claude costs, in US dollars per million tokens, as evals/pricing.json records it (a test
keeps the two equal). The dollar caps (triage's, the planner's, TC's monthly) price each call by
the model that served it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rates:
    input: float
    cached_input: float
    cache_write: float
    output: float


RATES: dict[str, Rates] = {
    "claude-haiku-5-5": Rates(0.10, 0.01, 0.125, 0.50),
    "claude-opus-5-5": Rates(4.00, 0.20, 5.00, 20.00),
}


def cost(rates: Rates, *, input: int, cached: int, cache_writes: int, output: int) -> float:
    """What a call cost: its input counts its cache reads and writes, billed at their own rates."""
    plain = input - cached - cache_writes
    spent = (
        plain * rates.input
        + cached * rates.cached_input
        + cache_writes * rates.cache_write
        + output * rates.output
    )
    return spent / 1e6


def worst(rates: Rates, bound: int, max_output: int, *, caches: bool = True) -> float:
    """The most a call can cost: every input token at the dearer of the input and cache-write
    rates (the input rate for a request that marks nothing for the cache), and its whole output
    allowance."""
    rate = max(rates.input, rates.cache_write) if caches else rates.input
    return (bound * rate + max_output * rates.output) / 1e6

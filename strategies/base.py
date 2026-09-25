# strategies/base.py
from abc import ABC, abstractmethod
import pandas as pd

class Signal:
    def __init__(self, symbol: str, timeframe: str, side: str,
                 price: float, timestamp):
        self.symbol = symbol
        self.timeframe = timeframe
        self.side = side          # "BUY" or "SELL"
        self.price = price
        self.timestamp = timestamp

class StrategyBase(ABC):
    name: str = "base"

    def __init__(self, symbol: str, timeframe: str, params: dict):
        self.symbol = symbol
        self.timeframe = timeframe
        self.params = params

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> Signal | None:
        """Return a Signal if the last closed candle triggers, else None."""

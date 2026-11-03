"""Abstract base class for exporters."""
from __future__ import annotations

from abc import ABC, abstractmethod

from config import Config
from processing.topology import Topology

class BaseExporter(ABC):
    """All exporters inherit from this."""

    def __init__(self, config: Config) -> None:
        self.config = config

    @abstractmethod
    def export(self, topology: Topology):
        ...

"""Starting jobs from the app, behind an interface so the app runs locally without Databricks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field


class JobTrigger(ABC):
    @abstractmethod
    def trigger(self, job_name: str, params: Mapping[str, str] | None = None) -> str:
        """Start a job run and return a run id. Does not wait for it to finish."""


@dataclass
class FakeJobTrigger(JobTrigger):
    """Records requests instead of running anything."""

    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def trigger(self, job_name: str, params: Mapping[str, str] | None = None) -> str:
        self.calls.append((job_name, dict(params or {})))
        return f"fake-run-{len(self.calls)}"

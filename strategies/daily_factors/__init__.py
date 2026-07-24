"""Point-in-time daily-factor portfolio research for the NSE price panel."""

from .engine import ResearchConfig, run_research

__all__ = ["ResearchConfig", "run_research"]

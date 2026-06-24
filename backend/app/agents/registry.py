"""Single source of truth for agent class lookup by name."""
from __future__ import annotations

from app.agents.analytics import AnalyticsAgent
from app.agents.base import Agent
from app.agents.designer import DesignerAgent
from app.agents.learning import LearningAgent
from app.agents.publisher import PublisherAgent
from app.agents.research import ResearchAgent
from app.agents.seo import SEOAgent
from app.agents.strategist import StrategistAgent
from app.agents.video import VideoAgent
from app.agents.writer import WriterAgent

AGENTS: dict[str, type[Agent]] = {
    "research":   ResearchAgent,
    "strategist": StrategistAgent,
    "writer":     WriterAgent,
    "seo":        SEOAgent,
    "designer":   DesignerAgent,
    "video":      VideoAgent,
    "publisher":  PublisherAgent,
    "analytics":  AnalyticsAgent,
    "learning":   LearningAgent,
}


def get_agent(name: str) -> Agent:
    cls = AGENTS.get(name)
    if not cls:
        raise KeyError(f"unknown agent: {name}")
    return cls()

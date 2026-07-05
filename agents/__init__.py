# LifeVault Agents Package
# Multi-agent system built with Google ADK
#
# Entry point: agent.py (contains root_agent)
# Run with: adk run agents/ OR adk web agents/

from .agent import root_agent

__all__ = ["root_agent"]

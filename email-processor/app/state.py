"""
Module-level singletons shared across the app.

Keeping the RulesEngine here (rather than in main.py) avoids circular
imports when routers need to call engine.reload() after a DB mutation.
"""

from app.config import get_settings
from app.rules.engine import RulesEngine

engine = RulesEngine()

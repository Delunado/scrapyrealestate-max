"""Cross-cutting services: search orchestration, change detection,
notification routing, and scheduling (see `TASKS.md` Phase 5-7).
"""

from scrapyrealestate.services.search_orchestration import (
    PortalAttemptOutcome,
    SearchOrchestrationService,
    SearchRunOutcome,
)
from scrapyrealestate.services.scheduler import InProcessScheduler
from scrapyrealestate.services.search_triggering import SearchTriggerService

__all__ = [
    "PortalAttemptOutcome",
    "SearchOrchestrationService",
    "SearchRunOutcome",
    "SearchTriggerService",
    "InProcessScheduler",
]

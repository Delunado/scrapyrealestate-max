"""Cross-cutting services: search orchestration, change detection,
notification routing, and scheduling (see `TASKS.md` Phase 5-7).
"""

from scrapyrealestate.services.search_orchestration import (
    PortalAttemptOutcome,
    SearchOrchestrationService,
    SearchRunOutcome,
)

__all__ = [
    "PortalAttemptOutcome",
    "SearchOrchestrationService",
    "SearchRunOutcome",
]

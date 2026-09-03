"""A bounded, single-worker coordinator for optional summary refreshes."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zglab_rag.conversation.summary import ConversationSummaryService

logger = logging.getLogger(__name__)


class SummaryCoordinator:
    """Run at most one refresh globally; drop extra work instead of queueing it."""

    def __init__(self, summary_service: ConversationSummaryService) -> None:
        self._service = summary_service
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="summary")
        self._lock = threading.Lock()
        self._active = False
        self._shutdown = False

    def schedule(self, *, owner_user_id: int, conversation_id: int) -> bool:
        """Schedule only when the bounded worker is idle; this never blocks asks."""
        with self._lock:
            if self._shutdown or self._active:
                return False
            self._active = True
        try:
            self._executor.submit(self._run, owner_user_id, conversation_id)
        except RuntimeError:
            with self._lock:
                self._active = False
            return False
        return True

    def _run(self, owner_user_id: int, conversation_id: int) -> None:
        try:
            self._service.refresh_summary(
                owner_user_id=owner_user_id,
                conversation_id=conversation_id,
            )
        except Exception:
            logger.exception(
                "conversation_summary_worker_failed conversation_id=%s", conversation_id
            )
        finally:
            with self._lock:
                self._active = False

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        self._executor.shutdown(wait=wait, cancel_futures=True)

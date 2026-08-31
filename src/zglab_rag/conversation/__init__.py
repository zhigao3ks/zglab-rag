"""Framework-free Conversation persistence foundation (Phase 15A1).

This package deliberately owns only conversation and message storage. It is
not wired to HTTP, SSE, prompt assembly, or the existing ask runtime yet.
"""

from zglab_rag.conversation.database import CONVERSATION_SCHEMA_VERSION, ConversationDatabase
from zglab_rag.conversation.models import Conversation, Message, MessageRole
from zglab_rag.conversation.repositories import ConversationRepository, MessageRepository

__all__ = [
    "CONVERSATION_SCHEMA_VERSION",
    "Conversation",
    "ConversationDatabase",
    "ConversationRepository",
    "Message",
    "MessageRepository",
    "MessageRole",
]

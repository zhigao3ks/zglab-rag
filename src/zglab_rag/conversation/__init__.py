"""Framework-free Conversation persistence and bounded context domain."""

from zglab_rag.conversation.context import (
    ConversationContext,
    ConversationContextMessage,
    assemble_conversation_context,
)
from zglab_rag.conversation.database import CONVERSATION_SCHEMA_VERSION, ConversationDatabase
from zglab_rag.conversation.models import Conversation, Message, MessageRole
from zglab_rag.conversation.repositories import ConversationRepository, MessageRepository

__all__ = [
    "CONVERSATION_SCHEMA_VERSION",
    "Conversation",
    "ConversationContext",
    "ConversationContextMessage",
    "ConversationDatabase",
    "ConversationRepository",
    "Message",
    "MessageRepository",
    "MessageRole",
    "assemble_conversation_context",
]

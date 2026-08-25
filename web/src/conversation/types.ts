/**
 * Conversation view-model types shared by assistant components.
 *
 * Phase 11 moved the assistant shell from App.vue into
 * views/AssistantView.vue; these types live in their own module so
 * components never import from a view.
 */

import type { FrontendErrorCode, PublicAskResponse, StreamStage } from "../api/contracts";

/** One assistant turn. */
export type AssistantTurn =
  | { phase: "pending"; stage: StreamStage | null }
  | { phase: "completed"; completed: PublicAskResponse }
  | { phase: "error"; code: FrontendErrorCode; requestId: string | null };

export type ChatMessage =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "assistant"; turn: AssistantTurn };

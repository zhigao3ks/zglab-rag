/**
 * Public API contracts, mirroring the narrow Phase 9A/9B backend schemas.
 * Internal diagnostics, scores, provider details and token usage are never
 * part of these types and must never be rendered.
 */

export type PublicStatus = "answered" | "insufficient_evidence";

export type PublicErrorCode =
  | "INVALID_REQUEST"
  | "RATE_LIMITED"
  | "SERVICE_BUSY"
  | "GENERATION_TIMEOUT"
  | "PROVIDER_UNAVAILABLE"
  | "INTERNAL_ERROR"
  // Phase 11 security error codes.
  | "AUTHENTICATION_REQUIRED"
  | "INVALID_CREDENTIALS"
  | "ACCOUNT_UNAVAILABLE"
  | "CSRF_REJECTED"
  | "QUOTA_EXCEEDED"
  | "SERVICE_DISABLED"
  | "API_RETIRED";

/** Frontend-only code for fetch/network/protocol failures. */
export type FrontendErrorCode = PublicErrorCode | "NETWORK";

export interface PublicSource {
  id: string;
  title: string;
  section: string[];
  source_path: string;
}

export interface PublicAskResponse {
  request_id: string;
  status: PublicStatus;
  answer: string;
  sources: PublicSource[];
}

export interface PublicErrorDetail {
  code: PublicErrorCode;
  message: string;
}

export interface PublicErrorResponse {
  request_id: string;
  error: PublicErrorDetail;
}

export type StreamStage = "accepted" | "retrieving" | "generating" | "validating";

export interface PublicStreamStatus {
  request_id: string;
  stage: StreamStage;
}

/** Typed union of everything the SSE stream can deliver. */
export type StreamEvent =
  | { kind: "stage"; stage: StreamStage; requestId: string }
  | { kind: "completed"; completed: PublicAskResponse }
  | { kind: "error"; error: PublicErrorResponse };

export const QUESTION_MAX_LENGTH = 1000;

/** Visitor-facing stage labels. No internal implementation details. */
export const STAGE_LABELS: Record<StreamStage, string> = {
  accepted: "已接收问题…",
  retrieving: "正在检索公开知识库…",
  generating: "正在整理回答…",
  validating: "正在核验引用…",
};

/** Visitor-facing error messages; raw server messages are never shown. */
export const ERROR_LABELS: Record<FrontendErrorCode, string> = {
  INVALID_REQUEST: "请求内容似乎有问题，请检查后重试。",
  RATE_LIMITED: "请求有点频繁，请稍后再试。",
  SERVICE_BUSY: "当前正在处理其他请求，请稍后再试。",
  GENERATION_TIMEOUT: "本次回答耗时过长，可以稍后重试。",
  PROVIDER_UNAVAILABLE: "回答服务暂时不可用，请稍后再试。",
  INTERNAL_ERROR: "服务暂时出现问题，请稍后再试。",
  AUTHENTICATION_REQUIRED: "请先登录后再使用助手。",
  INVALID_CREDENTIALS: "用户名或密码错误。",
  ACCOUNT_UNAVAILABLE: "账号当前不可用，请联系管理员。",
  CSRF_REJECTED: "请求未通过安全校验，请刷新页面后重试。",
  QUOTA_EXCEEDED: "今日或本分钟的使用额度已达上限，请稍后再试。",
  SERVICE_DISABLED: "回答服务当前已被临时关闭，请稍后再试。",
  API_RETIRED: "该接口版本已停用，请刷新页面。",
  NETWORK: "网络连接异常，请稍后重试。",
};

export function isPublicErrorCode(value: unknown): value is PublicErrorCode {
  return (
    value === "INVALID_REQUEST" ||
    value === "RATE_LIMITED" ||
    value === "SERVICE_BUSY" ||
    value === "GENERATION_TIMEOUT" ||
    value === "PROVIDER_UNAVAILABLE" ||
    value === "INTERNAL_ERROR" ||
    value === "AUTHENTICATION_REQUIRED" ||
    value === "INVALID_CREDENTIALS" ||
    value === "ACCOUNT_UNAVAILABLE" ||
    value === "CSRF_REJECTED" ||
    value === "QUOTA_EXCEEDED" ||
    value === "SERVICE_DISABLED" ||
    value === "API_RETIRED"
  );
}

export function isStreamStage(value: unknown): value is StreamStage {
  return (
    value === "accepted" ||
    value === "retrieving" ||
    value === "generating" ||
    value === "validating"
  );
}

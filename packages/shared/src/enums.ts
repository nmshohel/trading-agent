export const UserRole = {
  OWNER: "OWNER",
  VIEWER: "VIEWER",
} as const;
export type UserRole = (typeof UserRole)[keyof typeof UserRole];

export const AccountMode = {
  DEMO: "DEMO",
  LIVE: "LIVE",
} as const;
export type AccountMode = (typeof AccountMode)[keyof typeof AccountMode];

export const BrokerAccountType = {
  STANDARD: "STANDARD",
  CENT: "CENT",
} as const;
export type BrokerAccountType =
  (typeof BrokerAccountType)[keyof typeof BrokerAccountType];

export const TradeDirection = {
  BUY: "BUY",
  SELL: "SELL",
} as const;
export type TradeDirection =
  (typeof TradeDirection)[keyof typeof TradeDirection];

export const ProposalStatus = {
  DRAFT: "DRAFT",
  PENDING_APPROVAL: "PENDING_APPROVAL",
  APPROVED: "APPROVED",
  REJECTED: "REJECTED",
  EXPIRED: "EXPIRED",
  RISK_REJECTED: "RISK_REJECTED",
  AI_VETOED: "AI_VETOED",
  EXECUTING: "EXECUTING",
  EXECUTED: "EXECUTED",
  EXECUTION_FAILED: "EXECUTION_FAILED",
  CANCELLED: "CANCELLED",
} as const;
export type ProposalStatus =
  (typeof ProposalStatus)[keyof typeof ProposalStatus];

export const AiAnalysisStatus = {
  COMPLETED: "COMPLETED",
  VETOED: "VETOED",
  UNAVAILABLE: "UNAVAILABLE",
  SKIPPED: "SKIPPED",
} as const;
export type AiAnalysisStatus =
  (typeof AiAnalysisStatus)[keyof typeof AiAnalysisStatus];

export const AiFallbackMode = {
  DETERMINISTIC_ONLY: "DETERMINISTIC_ONLY",
  SKIP: "SKIP",
} as const;
export type AiFallbackMode =
  (typeof AiFallbackMode)[keyof typeof AiFallbackMode];

export const ApprovalDecision = {
  APPROVE: "APPROVE",
  REJECT: "REJECT",
} as const;
export type ApprovalDecision =
  (typeof ApprovalDecision)[keyof typeof ApprovalDecision];

export const OrderActionType = {
  OPEN: "OPEN",
  CLOSE: "CLOSE",
} as const;
export type OrderActionType =
  (typeof OrderActionType)[keyof typeof OrderActionType];

export const OrderStatus = {
  REQUESTED: "REQUESTED",
  ACCEPTED: "ACCEPTED",
  PARTIALLY_FILLED: "PARTIALLY_FILLED",
  FILLED: "FILLED",
  REJECTED: "REJECTED",
  CANCELLED: "CANCELLED",
  UNKNOWN: "UNKNOWN",
} as const;
export type OrderStatus = (typeof OrderStatus)[keyof typeof OrderStatus];

export const PositionStatus = {
  OPEN: "OPEN",
  CLOSING: "CLOSING",
  CLOSED: "CLOSED",
  UNKNOWN: "UNKNOWN",
} as const;
export type PositionStatus =
  (typeof PositionStatus)[keyof typeof PositionStatus];

export const EventSeverity = {
  INFO: "INFO",
  WARN: "WARN",
  ERROR: "ERROR",
  CRITICAL: "CRITICAL",
} as const;
export type EventSeverity =
  (typeof EventSeverity)[keyof typeof EventSeverity];

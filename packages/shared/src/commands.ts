import { z } from "zod";

export const ExecuteProposalCommandSchema = z.object({
  type: z.literal("EXECUTE_PROPOSAL"),
  commandId: z.string().min(1),
  idempotencyKey: z.string().min(1),
  createdAt: z.string().datetime(),
  payload: z.object({
    proposalId: z.string().min(1),
    approvalId: z.string().min(1),
    accountLogin: z.string().min(1),
    symbol: z.string().min(1),
    autoApproved: z.boolean().optional(),
  }),
});

export const ClosePositionCommandSchema = z.object({
  type: z.literal("CLOSE_POSITION"),
  commandId: z.string().min(1),
  idempotencyKey: z.string().min(1),
  createdAt: z.string().datetime(),
  payload: z.object({
    positionId: z.string().min(1),
    brokerPositionId: z.string().min(1),
    accountLogin: z.string().min(1),
    symbol: z.string().min(1),
    requestedByUserId: z.string().min(1),
  }),
});

export const TradingCommandSchema = z.discriminatedUnion("type", [
  ExecuteProposalCommandSchema,
  ClosePositionCommandSchema,
]);

export type ExecuteProposalCommand = z.infer<
  typeof ExecuteProposalCommandSchema
>;
export type ClosePositionCommand = z.infer<typeof ClosePositionCommandSchema>;
export type TradingCommand = z.infer<typeof TradingCommandSchema>;

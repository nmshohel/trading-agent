import { z } from "zod";

export const SseEventTypeSchema = z.enum([
  "quote.updated",
  "proposal.created",
  "proposal.updated",
  "proposal.expired",
  "order.updated",
  "position.updated",
  "position.closed",
  "system.health",
  "risk.alert",
  "settings.updated",
]);

export type SseEventType = z.infer<typeof SseEventTypeSchema>;

export const SseEnvelopeSchema = z.object({
  id: z.string().min(1),
  type: SseEventTypeSchema,
  timestamp: z.string().datetime(),
  data: z.record(z.unknown()),
});

export type SseEnvelope = z.infer<typeof SseEnvelopeSchema>;

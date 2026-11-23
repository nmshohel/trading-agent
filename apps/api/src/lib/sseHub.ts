import type { Response } from "express";
import type { SseEnvelope } from "@trading-agent/shared";

interface ConnectedClient {
  id: string;
  userId: string;
  res: Response;
  heartbeatTimer: NodeJS.Timeout;
}

export interface SseHubOptions {
  maxConnectionsPerUser?: number;
  heartbeatIntervalMs?: number;
  replayBufferSize?: number;
}

export class SseHub {
  private readonly clients = new Map<string, ConnectedClient>();
  private readonly replayBuffer: SseEnvelope[] = [];
  private readonly maxPerUser: number;
  private readonly heartbeatMs: number;
  private readonly replayLimit: number;

  constructor(opts: SseHubOptions = {}) {
    this.maxPerUser = opts.maxConnectionsPerUser ?? 5;
    this.heartbeatMs = opts.heartbeatIntervalMs ?? 20_000;
    this.replayLimit = opts.replayBufferSize ?? 100;
  }

  getConnectionCount(userId?: string): number {
    if (!userId) return this.clients.size;
    let count = 0;
    for (const c of this.clients.values()) {
      if (c.userId === userId) count++;
    }
    return count;
  }

  canAccept(userId: string): boolean {
    return this.getConnectionCount(userId) < this.maxPerUser;
  }

  register(
    clientId: string,
    userId: string,
    res: Response,
    lastEventId?: string,
  ): () => void {
    const heartbeatTimer = setInterval(() => {
      if (!res.writableEnded) {
        res.write(": ping\n\n");
      }
    }, this.heartbeatMs);
    heartbeatTimer.unref?.();

    const client: ConnectedClient = {
      id: clientId,
      userId,
      res,
      heartbeatTimer,
    };
    this.clients.set(clientId, client);

    // Send initial connection comment immediately to flush proxy headers
    res.write(": connected\n\n");

    // Replay missed events if client supplied Last-Event-ID
    if (lastEventId) {
      const idx = this.replayBuffer.findIndex((e) => e.id === lastEventId);
      if (idx >= 0) {
        for (const missed of this.replayBuffer.slice(idx + 1)) {
          this.writeFrame(res, missed);
        }
      }
    }

    return () => {
      this.unregister(clientId);
    };
  }

  unregister(clientId: string): void {
    const client = this.clients.get(clientId);
    if (!client) return;
    clearInterval(client.heartbeatTimer);
    this.clients.delete(clientId);
  }

  broadcast(event: SseEnvelope): void {
    this.replayBuffer.push(event);
    if (this.replayBuffer.length > this.replayLimit) {
      this.replayBuffer.shift();
    }

    for (const client of this.clients.values()) {
      if (client.res.writableEnded) {
        this.unregister(client.id);
        continue;
      }
      this.writeFrame(client.res, event);
    }
  }

  closeAll(): void {
    for (const [id, client] of this.clients.entries()) {
      clearInterval(client.heartbeatTimer);
      if (!client.res.writableEnded) {
        client.res.end();
      }
      this.clients.delete(id);
    }
  }

  private writeFrame(res: Response, event: SseEnvelope): void {
    res.write(`id: ${event.id}\n`);
    res.write(`event: ${event.type}\n`);
    res.write(`data: ${JSON.stringify(event)}\n\n`);
  }
}

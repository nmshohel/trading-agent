"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

export type SseConnectionState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

export interface LiveQuoteEvent {
  symbol: string;
  bid: string;
  ask: string;
  spread: string;
  quoteTime: string;
  candleClosed?: boolean;
}

export interface LiveHealthEvent {
  workerId?: string;
  mt5Connected?: boolean;
  terminalTradeAllowed?: boolean;
  accountTradeAllowed?: boolean;
  accountMode?: "DEMO" | "LIVE" | null;
  symbol?: string | null;
  lastHeartbeatAt?: string;
  killSwitchActive?: boolean;
  isPaused?: boolean;
}

export interface LiveAlertEvent {
  code?: string;
  severity?: string;
  message: string;
}

export function useTradingStream(enabled = true) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<SseConnectionState>(
    enabled ? "connecting" : "disconnected",
  );
  const [latestQuote, setLatestQuote] = useState<LiveQuoteEvent | null>(null);
  const [latestHealth, setLatestHealth] = useState<LiveHealthEvent | null>(
    null,
  );
  const [latestAlert, setLatestAlert] = useState<LiveAlertEvent | null>(null);
  const hasConnectedOnce = useRef(false);

  useEffect(() => {
    if (!enabled) {
      setStatus("disconnected");
      return;
    }

    setStatus("connecting");
    const es = new EventSource("/api/v1/stream", { withCredentials: true });

    es.onopen = () => {
      setStatus("connected");
      if (hasConnectedOnce.current) {
        void queryClient.invalidateQueries({ queryKey: ["market-xauusd"] });
        void queryClient.invalidateQueries({ queryKey: ["market-candles"] });
        void queryClient.invalidateQueries({ queryKey: ["system-health"] });
        void queryClient.invalidateQueries({ queryKey: ["proposals"] });
        void queryClient.invalidateQueries({ queryKey: ["positions"] });
        void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      }
      hasConnectedOnce.current = true;
    };

    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) {
        setStatus("disconnected");
      } else {
        setStatus("reconnecting");
      }
    };

    const handleQuote = (ev: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(ev.data) as { data?: LiveQuoteEvent };
        if (envelope.data) {
          setLatestQuote(envelope.data);
          if (envelope.data.candleClosed) {
            void queryClient.invalidateQueries({
              queryKey: ["market-candles"],
            });
          }
        }
      } catch {
        // Ignore malformed frame
      }
    };

    const handleHealth = (ev: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(ev.data) as { data?: LiveHealthEvent };
        if (envelope.data) {
          setLatestHealth((prev) => ({ ...prev, ...envelope.data }));
          void queryClient.invalidateQueries({ queryKey: ["system-health"] });
          void queryClient.invalidateQueries({
            queryKey: ["dashboard-summary"],
          });
        }
      } catch {
        // Ignore malformed frame
      }
    };

    const handleProposalCreated = (ev: MessageEvent<string>) => {
      void queryClient.invalidateQueries({ queryKey: ["proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      try {
        const envelope = JSON.parse(ev.data) as {
          data?: { symbol?: string; direction?: string };
        };
        if (envelope.data) {
          setLatestAlert({
            code: "PROPOSAL_CREATED",
            severity: "INFO",
            message: `New proposal: ${envelope.data.direction ?? ""} ${envelope.data.symbol ?? "XAUUSD"} awaiting approval`,
          });
          if (
            typeof window !== "undefined" &&
            "Notification" in window &&
            Notification.permission === "granted"
          ) {
            new Notification("New Trade Proposal Ready", {
              body: `${envelope.data.direction ?? ""} ${envelope.data.symbol ?? "XAUUSD"} awaiting approval`,
            });
          }
        }
      } catch {
        // Non-fatal
      }
    };

    const handleProposalChanged = () => {
      void queryClient.invalidateQueries({ queryKey: ["proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["proposal"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    };

    const handleOrderOrPosition = (ev: MessageEvent<string>) => {
      void queryClient.invalidateQueries({ queryKey: ["positions"] });
      void queryClient.invalidateQueries({ queryKey: ["orders"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      void queryClient.invalidateQueries({ queryKey: ["reports"] });
      try {
        const envelope = JSON.parse(ev.data) as {
          type?: string;
          data?: { symbol?: string; status?: string; realizedProfit?: string };
        };
        if (envelope.type === "position.closed") {
          setLatestAlert({
            code: "POSITION_CLOSED",
            severity: "INFO",
            message: `Position closed (${envelope.data?.symbol ?? "XAUUSD"}) — P/L: ${envelope.data?.realizedProfit ?? "reconciled"}`,
          });
        }
      } catch {
        // Non-fatal
      }
    };

    const handleSettingsUpdated = () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      void queryClient.invalidateQueries({ queryKey: ["blackouts"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    };

    const handleAlert = (ev: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(ev.data) as { data?: LiveAlertEvent };
        if (envelope.data?.message) {
          setLatestAlert(envelope.data);
        }
      } catch {
        // Ignore malformed alert
      }
    };

    es.addEventListener("quote.updated", handleQuote as EventListener);
    es.addEventListener("system.health", handleHealth as EventListener);
    es.addEventListener(
      "proposal.created",
      handleProposalCreated as EventListener,
    );
    es.addEventListener(
      "proposal.updated",
      handleProposalChanged as EventListener,
    );
    es.addEventListener(
      "proposal.expired",
      handleProposalChanged as EventListener,
    );
    es.addEventListener("order.updated", handleOrderOrPosition as EventListener);
    es.addEventListener(
      "position.updated",
      handleOrderOrPosition as EventListener,
    );
    es.addEventListener(
      "position.closed",
      handleOrderOrPosition as EventListener,
    );
    es.addEventListener(
      "settings.updated",
      handleSettingsUpdated as EventListener,
    );
    es.addEventListener("risk.alert", handleAlert as EventListener);

    return () => {
      es.close();
    };
  }, [enabled, queryClient]);

  return {
    status,
    latestQuote,
    latestHealth,
    latestAlert,
    dismissAlert: () => setLatestAlert(null),
  };
}

"use client";

import { useState, useRef, useEffect } from "react";
import {
  SendIcon,
  BotIcon,
  UserIcon,
  OrbitIcon,
  SpinnerIcon,
} from "@/components/admin/AdminIcons";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface SwarmMessage {
  role: string;
  content: string;
  agent?: string; // e.g., "Sprint Manager", "Orchestrator"
}

export default function SwarmChatPage() {
  const [messages, setMessages] = useState<SwarmMessage[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Polling for new messages
  useEffect(() => {
    const fetchHistory = async () => {
      try {
        const res = await fetch(`${API_BASE}/swarm/chat`);
        if (res.ok) {
          const data = await res.json();
          setMessages(data.messages);
        }
      } catch (err) {
        console.error("Failed to fetch swarm history", err);
      } finally {
        // history poll resolves silently — the shell shows overall health
      }
    };

    fetchHistory();
    const interval = setInterval(fetchHistory, 3000); // Poll every 3 seconds
    return () => clearInterval(interval);
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || isSending) return;
    
    const newMsg: SwarmMessage = { role: "user", content: input };
    setMessages((prev) => [...prev, newMsg]);
    setInput("");
    setIsSending(true);

    try {
      await fetch(`${API_BASE}/swarm/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newMsg),
      });
      // The polling interval will pick up the agent's response
    } catch (err) {
      console.error("Failed to send message", err);
    } finally {
      setIsSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div data-testid="swarm-page" className="flex flex-col h-[calc(100vh-64px)] text-[var(--sr-text-primary)]">
      {/* Header */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-md bg-[var(--sr-dusk-interactive)] border border-[var(--sr-border-strong)] flex items-center justify-center">
            <OrbitIcon className="w-4 h-4 text-[var(--sr-dusk)]" />
          </div>
          <div>
            <h1 className="heading-display text-base text-[var(--sr-text-primary)]">Unified Agent Swarm</h1>
            <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">Talk to Sprint Manager, Spec Writer, and other autonomous agents.</p>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[75%] rounded-md p-4 border ${
              m.role === "user"
                ? "bg-[var(--sr-dusk-interactive)] text-[var(--sr-text-primary)] border-[var(--sr-border-strong)]"
                : m.role === "system"
                ? "bg-[var(--sr-surface-deep)] text-[var(--sr-text-primary)] border-[var(--sr-dusk)]/40"
                : "bg-[var(--sr-surface-card)] text-[var(--sr-text-primary)] border-[var(--sr-border-subtle)]"
            }`}>
              <div className="flex items-center gap-2 mb-2">
                {m.role === "user" ? (
                  <UserIcon className="w-4 h-4 text-[var(--sr-link)]" />
                ) : (
                  <BotIcon className="w-4 h-4 text-[var(--sr-dusk)]" />
                )}
                <span className="admin-mono-font text-[9px] tracking-[0.14em] uppercase text-[var(--sr-text-label)]">
                  {m.role === "user" ? "You" : m.agent || "Agent"}
                </span>
              </div>
              <div className="text-[13px] leading-relaxed whitespace-pre-wrap">
                {m.content}
              </div>
            </div>
          </div>
        ))}
        {isSending && (
          <div className="flex justify-start">
             <div className="bg-[var(--sr-surface-card)] border border-[var(--sr-border-subtle)] rounded-md p-4 flex items-center gap-3">
                <SpinnerIcon className="w-4 h-4 animate-spin text-[var(--sr-dusk)]" />
                <span className="admin-mono-font text-[10px] text-[var(--sr-text-secondary)] uppercase tracking-[0.14em]">Sending signal…</span>
             </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex-shrink-0 p-6 bg-[var(--sr-surface-card)] border-t border-[var(--sr-border-subtle)]">
        <div className="max-w-4xl mx-auto relative">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Tell the swarm what to build, or ask the Sprint Manager for an update..."
            className="w-full bg-[var(--sr-surface-deep)] border border-[var(--sr-border-strong)] text-[var(--sr-text-primary)] placeholder:text-[var(--sr-text-tertiary)] rounded-md px-4 py-4 pr-14 text-[13px] resize-none focus:outline-none focus:border-[var(--sr-dusk)] focus:ring-1 focus:ring-[var(--sr-dusk)]/40 transition-all"
            rows={3}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isSending}
            className="absolute bottom-4 right-4 p-2.5 bg-[var(--sr-dusk)] hover:bg-[var(--sr-link)] text-white rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <SendIcon className="w-4 h-4" />
          </button>
        </div>
        <div className="max-w-4xl mx-auto mt-2.5 text-center">
            <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-tertiary)]">Shift + Enter for new line · Enter sends via Temporal signal</span>
        </div>
      </div>
    </div>
  );
}

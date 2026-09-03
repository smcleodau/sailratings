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
    <div className="flex flex-col h-[calc(100vh-60px)] bg-[var(--sr-surface-page)] text-[var(--sr-text-primary)]">
      {/* Header */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] flex items-center justify-between shadow-sm">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[var(--sr-action)]/15 border border-[var(--sr-action)]/30 flex items-center justify-center">
            <OrbitIcon className="w-4 h-4 text-[var(--sr-action)]" />
          </div>
          <div>
            <h1 className="font-bold text-[var(--sr-text-primary)] text-sm font-display uppercase tracking-wide">Unified Agent Swarm</h1>
            <p className="text-xs text-[var(--sr-text-secondary)] font-mono">Talk to Sprint Manager, Spec Writer, and other autonomous agents.</p>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"} animate-in fade-in slide-in-from-bottom-2`}>
            <div className={`max-w-[75%] rounded-xl p-4 shadow-md border ${
              m.role === "user" 
                ? "bg-[var(--sr-surface-interactive)] text-[var(--sr-text-primary)] border-[var(--sr-marine-600)]" 
                : m.role === "system"
                ? "bg-[var(--sr-surface-deep)] text-[var(--sr-text-primary)] border-[var(--sr-action)]/30"
                : "bg-[var(--sr-surface-card)] text-[var(--sr-text-primary)] border-[var(--sr-border-strong)]"
            }`}>
              <div className="flex items-center gap-2 mb-2">
                {m.role === "user" ? (
                  <UserIcon className="w-4 h-4 text-[var(--sr-marine-200)]" />
                ) : (
                  <BotIcon className="w-4 h-4 text-[var(--sr-action)]" />
                )}
                <span className="text-[10px] font-mono tracking-wider uppercase font-semibold text-[var(--sr-text-label)]">
                  {m.role === "user" ? "You" : m.agent || "Agent"}
                </span>
              </div>
              <div className="text-sm leading-relaxed whitespace-pre-wrap font-sans">
                {m.content}
              </div>
            </div>
          </div>
        ))}
        {isSending && (
          <div className="flex justify-start">
             <div className="bg-[var(--sr-surface-card)] border border-[var(--sr-border-strong)] rounded-xl p-4 flex items-center gap-3 shadow-md">
                <SpinnerIcon className="w-4 h-4 animate-spin text-[var(--sr-action)]" />
                <span className="text-xs font-mono text-[var(--sr-text-secondary)] uppercase tracking-wider">Sending signal...</span>
             </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex-shrink-0 p-6 bg-[var(--sr-surface-card)] border-t border-[var(--sr-border-subtle)]">
        <div className="max-w-4xl mx-auto relative group">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Tell the swarm what to build, or ask the Sprint Manager for an update..."
            className="w-full bg-[var(--sr-surface-deep)] border border-[var(--sr-border-strong)] text-[var(--sr-text-primary)] placeholder:text-[var(--sr-text-tertiary)] rounded-xl px-4 py-4 pr-14 text-sm font-sans resize-none focus:outline-none focus:border-[var(--sr-action)] focus:ring-1 focus:ring-[var(--sr-action)]/50 transition-all shadow-inner"
            rows={3}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isSending}
            className="absolute bottom-4 right-4 p-2.5 bg-[var(--sr-action)] hover:bg-[var(--sr-action-pressed)] text-[var(--sr-action-text)] rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-md font-bold"
          >
            <SendIcon className="w-4 h-4" />
          </button>
        </div>
        <div className="max-w-4xl mx-auto mt-2.5 text-center">
            <span className="text-[10px] text-[var(--sr-text-tertiary)] font-mono">Shift + Enter for new line. Enter to send via Temporal Signal.</span>
        </div>
      </div>
    </div>
  );
}

"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Send, ChevronDown, ChevronRight, Check, X, Database, AlertTriangle, MessageSquare } from "lucide-react";

/* ── Types ────────────────────────────────────────────────────────────── */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface AdminSSEEvent {
  type: "text" | "query" | "proposed_change" | "done" | "error";
  data: string | QueryData | ProposedChangeData | Record<string, never>;
}

interface QueryData {
  sql: string;
  explanation: string;
}

interface ProposedChangeData {
  sql: string;
  explanation: string;
  affected_rows_estimate: string;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  queries?: QueryData[];
  proposedChanges?: ProposedChangeItem[];
}

interface ProposedChangeItem {
  id: string;
  data: ProposedChangeData;
  status: "pending" | "confirmed" | "rejected" | "executing" | "executed" | "error";
  result?: { status: string; rows_affected: number };
  error?: string;
}

/* ── SSE Stream Parser ────────────────────────────────────────────────── */

async function* streamAdminChat(
  message: string,
  token: string,
): AsyncGenerator<AdminSSEEvent, void, unknown> {
  const res = await fetch(`${API_BASE}/admin/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ message }),
  });

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new Error("Unauthorized. Check your password.");
    }
    throw new Error(`Request failed: ${res.status}`);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error("No readable stream in response");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith("data:")) continue;

        const jsonStr = trimmed.slice(5).trim();
        if (!jsonStr || jsonStr === "[DONE]") continue;

        try {
          const event: AdminSSEEvent = JSON.parse(jsonStr);
          yield event;

          if (event.type === "done") {
            return;
          }
        } catch {
          // Skip malformed JSON lines
        }
      }
    }

    // Process remaining buffer
    if (buffer.trim()) {
      const trimmed = buffer.trim();
      if (trimmed.startsWith("data:")) {
        const jsonStr = trimmed.slice(5).trim();
        if (jsonStr && jsonStr !== "[DONE]") {
          try {
            const event: AdminSSEEvent = JSON.parse(jsonStr);
            yield event;
          } catch {
            // Skip
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/* ── Execute Endpoint ─────────────────────────────────────────────────── */

async function executeChange(
  sql: string,
  token: string,
): Promise<{ status: string; rows_affected: number }> {
  const res = await fetch(`${API_BASE}/admin/execute`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ sql }),
  });

  if (!res.ok) {
    throw new Error(`Execute failed: ${res.status}`);
  }

  return res.json();
}

/* ── Example Prompts ──────────────────────────────────────────────────── */

const EXAMPLE_PROMPTS = [
  "Show me all twilight race results",
  "Which boats are racing on non-spinnaker certificates?",
  "How many Sunfast 3300s do we actually have?",
  "What race results do we have for SUN FISH?",
];

/* ── Login Gate ───────────────────────────────────────────────────────── */

function LoginGate({ onLogin }: { onLogin: (token: string) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!password.trim()) {
      setError("Please enter a password.");
      return;
    }
    setError("");
    onLogin(password.trim());
  };

  return (
    <div className="min-h-screen bg-navy flex items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="heading-serif text-3xl text-white/90 mb-2">
            Data Admin
          </h1>
          <p className="body-text text-white/40 text-sm">
            sailratings.com
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            ref={inputRef}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            className="w-full h-12 px-4 bg-navy-light border border-white/10 text-white placeholder:text-white/30 body-text text-base focus:border-copper focus:outline-none transition-colors"
          />
          {error && (
            <p className="body-text text-sm text-copper">{error}</p>
          )}
          <button
            type="submit"
            className="w-full h-12 bg-copper text-white body-text text-base font-medium hover:bg-copper-dark transition-colors"
          >
            Sign In
          </button>
        </form>
      </div>
    </div>
  );
}

/* ── Query Card (Collapsible) ─────────────────────────────────────────── */

function QueryCard({ query }: { query: QueryData }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="my-3 border border-white/10 bg-white/5 rounded-sm overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-white/5 transition-colors"
      >
        <Database size={14} strokeWidth={1.5} className="text-signal-light flex-shrink-0" />
        <span className="body-text text-sm text-white/70 flex-1">
          {query.explanation}
        </span>
        {expanded ? (
          <ChevronDown size={14} className="text-white/40 flex-shrink-0" />
        ) : (
          <ChevronRight size={14} className="text-white/40 flex-shrink-0" />
        )}
      </button>
      {expanded && (
        <div className="px-4 pb-3 border-t border-white/5">
          <pre className="data-mono text-xs text-signal-light/80 whitespace-pre-wrap break-all mt-2 leading-relaxed">
            {query.sql}
          </pre>
        </div>
      )}
    </div>
  );
}

/* ── Proposed Change Card ─────────────────────────────────────────────── */

function ProposedChangeCard({
  change,
  onConfirm,
  onReject,
}: {
  change: ProposedChangeItem;
  onConfirm: () => void;
  onReject: () => void;
}) {
  return (
    <div className="my-3 border border-copper/40 bg-copper/5 rounded-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-copper/20 flex items-center gap-2">
        <AlertTriangle size={14} strokeWidth={1.5} className="text-copper flex-shrink-0" />
        <span className="body-text text-xs uppercase tracking-wider text-copper font-medium">
          Proposed Change
        </span>
        {change.data.affected_rows_estimate && (
          <span className="data-mono text-xs text-white/40 ml-auto">
            ~{change.data.affected_rows_estimate} rows
          </span>
        )}
      </div>

      <div className="px-4 py-3">
        <p className="body-text text-sm text-white/80 mb-3">
          {change.data.explanation}
        </p>
        <pre className="data-mono text-xs text-copper/70 whitespace-pre-wrap break-all bg-black/20 px-3 py-2 rounded-sm leading-relaxed">
          {change.data.sql}
        </pre>
      </div>

      <div className="px-4 py-3 border-t border-copper/20">
        {change.status === "pending" && (
          <div className="flex items-center gap-3">
            <button
              onClick={onConfirm}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-signaltext-white text-sm body-text font-medium hover:bg-signal-light transition-colors"
            >
              <Check size={14} strokeWidth={2} />
              Confirm
            </button>
            <button
              onClick={onReject}
              className="flex items-center gap-1.5 px-4 py-1.5 border border-white/20 text-white/60 text-sm body-text hover:text-white hover:border-white/40 transition-colors"
            >
              <X size={14} strokeWidth={2} />
              Reject
            </button>
          </div>
        )}
        {change.status === "executing" && (
          <div className="flex items-center gap-2">
            <div
              className="w-3.5 h-3.5 border border-white/30 border-t-copper animate-spin"
              style={{ borderRadius: "50%" }}
            />
            <span className="body-text text-sm text-white/50 italic">
              Executing...
            </span>
          </div>
        )}
        {change.status === "executed" && change.result && (
          <div className="flex items-center gap-2">
            <Check size={14} strokeWidth={2} className="text-signal-light" />
            <span className="body-text text-sm text-signal-light">
              Executed successfully. {change.result.rows_affected} row{change.result.rows_affected !== 1 ? "s" : ""} affected.
            </span>
          </div>
        )}
        {change.status === "rejected" && (
          <span className="body-text text-sm text-white/40 italic">
            Change rejected.
          </span>
        )}
        {change.status === "error" && (
          <div className="flex items-center gap-2">
            <X size={14} strokeWidth={2} className="text-copper" />
            <span className="body-text text-sm text-copper">
              {change.error || "Execution failed."}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Main Admin Chat Page ─────────────────────────────────────────────── */

export default function AdminChatPage() {
  const [token, setToken] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);

  // Load token from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem("admin_token");
    if (stored) setToken(stored);
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Focus input after streaming ends
  useEffect(() => {
    if (!isStreaming) {
      inputRef.current?.focus();
    }
  }, [isStreaming]);

  const handleLogin = useCallback((password: string) => {
    localStorage.setItem("admin_token", password);
    setToken(password);
  }, []);

  const handleLogout = useCallback(() => {
    localStorage.removeItem("admin_token");
    setToken(null);
    setMessages([]);
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || !token || isStreaming) return;

      const userMsg: ChatMessage = {
        id: `user-${Date.now()}`,
        role: "user",
        content: text.trim(),
      };

      const assistantId = `assistant-${Date.now()}`;
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        queries: [],
        proposedChanges: [],
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setInput("");
      setIsStreaming(true);

      try {
        const stream = streamAdminChat(text.trim(), token);
        for await (const event of stream) {
          if (event.type === "text") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + (event.data as string) }
                  : m,
              ),
            );
          } else if (event.type === "query") {
            const queryData = event.data as QueryData;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, queries: [...(m.queries || []), queryData] }
                  : m,
              ),
            );
          } else if (event.type === "proposed_change") {
            const changeData = event.data as ProposedChangeData;
            const changeItem: ProposedChangeItem = {
              id: `change-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
              data: changeData,
              status: "pending",
            };
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      proposedChanges: [...(m.proposedChanges || []), changeItem],
                    }
                  : m,
              ),
            );
          } else if (event.type === "done") {
            break;
          } else if (event.type === "error") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + "\n\n[Error: " + (event.data as string) + "]" }
                  : m,
              ),
            );
            break;
          }
        }
      } catch (err) {
        const errorText =
          err instanceof Error ? err.message : "Something went wrong.";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: m.content || errorText }
              : m,
          ),
        );

        // If unauthorized, clear token
        if (errorText.includes("Unauthorized")) {
          localStorage.removeItem("admin_token");
          setToken(null);
        }
      } finally {
        setIsStreaming(false);
      }
    },
    [token, isStreaming],
  );

  const handleConfirmChange = useCallback(
    async (messageId: string, changeId: string) => {
      if (!token) return;

      // Find the change
      const msg = messages.find((m) => m.id === messageId);
      const change = msg?.proposedChanges?.find((c) => c.id === changeId);
      if (!change) return;

      // Set executing
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                proposedChanges: m.proposedChanges?.map((c) =>
                  c.id === changeId ? { ...c, status: "executing" as const } : c,
                ),
              }
            : m,
        ),
      );

      try {
        const result = await executeChange(change.data.sql, token);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? {
                  ...m,
                  proposedChanges: m.proposedChanges?.map((c) =>
                    c.id === changeId
                      ? { ...c, status: "executed" as const, result }
                      : c,
                  ),
                }
              : m,
          ),
        );
      } catch (err) {
        const errorText =
          err instanceof Error ? err.message : "Execution failed.";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? {
                  ...m,
                  proposedChanges: m.proposedChanges?.map((c) =>
                    c.id === changeId
                      ? { ...c, status: "error" as const, error: errorText }
                      : c,
                  ),
                }
              : m,
          ),
        );
      }
    },
    [token, messages],
  );

  const handleRejectChange = useCallback(
    (messageId: string, changeId: string) => {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                proposedChanges: m.proposedChanges?.map((c) =>
                  c.id === changeId ? { ...c, status: "rejected" as const } : c,
                ),
              }
            : m,
        ),
      );
    },
    [],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  // Auto-resize textarea
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  };

  /* ── Render ─────────────────────────────────────────────────────────── */

  if (!token) {
    return <LoginGate onLogin={handleLogin} />;
  }

  return (
    <div className="min-h-screen bg-navy flex flex-col">
      {/* Header */}
      <header className="flex-shrink-0 border-b border-white/10 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <MessageSquare size={18} strokeWidth={1.5} className="text-copper" />
          <h1 className="heading-serif text-xl text-white/90">
            Data Admin
          </h1>
          <span className="data-mono text-xs text-white/25 hidden sm:inline">
            sailratings.com
          </span>
        </div>
        <button
          onClick={handleLogout}
          className="body-text text-xs text-white/30 hover:text-white/60 transition-colors"
        >
          Sign Out
        </button>
      </header>

      {/* Chat Messages */}
      <div
        ref={chatContainerRef}
        className="flex-1 overflow-y-auto px-4 sm:px-6 py-6"
      >
        <div className="max-w-3xl mx-auto space-y-4">
          {/* Empty state with examples */}
          {messages.length === 0 && (
            <div className="py-16 text-center">
              <Database size={32} strokeWidth={1} className="text-white/15 mx-auto mb-6" />
              <h2 className="heading-serif text-2xl text-white/60 mb-2">
                Ask me about the data
              </h2>
              <p className="body-text text-sm text-white/30 mb-10 max-w-md mx-auto">
                I can query the database, investigate data quality issues, and
                propose fixes. Ask me anything about boats, certificates, or
                race results.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-lg mx-auto">
                {EXAMPLE_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    onClick={() => sendMessage(prompt)}
                    className="text-left px-4 py-3 border border-white/10 bg-white/5 hover:bg-white/10 hover:border-white/20 transition-colors rounded-sm"
                  >
                    <span className="body-text text-sm text-white/60">
                      {prompt}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Messages */}
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] sm:max-w-[75%] ${
                  msg.role === "user"
                    ? "bg-copper/20 border border-copper/30 px-4 py-3"
                    : "w-full max-w-none sm:max-w-[75%]"
                } rounded-sm`}
              >
                {msg.role === "user" ? (
                  <p className="body-text text-sm text-white/90 whitespace-pre-wrap">
                    {msg.content}
                  </p>
                ) : (
                  <div>
                    {/* Queries */}
                    {msg.queries?.map((q, i) => (
                      <QueryCard key={`q-${msg.id}-${i}`} query={q} />
                    ))}

                    {/* Text content */}
                    {msg.content && (
                      <div className="body-text text-sm text-white/80 whitespace-pre-wrap leading-relaxed px-1 py-1">
                        {msg.content}
                        {isStreaming &&
                          msg.id ===
                            messages[messages.length - 1]?.id && (
                            <span className="inline-block w-0.5 h-4 bg-copper ml-0.5 align-text-bottom streaming-pulse" />
                          )}
                      </div>
                    )}

                    {/* Loading state when nothing rendered yet */}
                    {isStreaming &&
                      msg.id === messages[messages.length - 1]?.id &&
                      !msg.content &&
                      (!msg.queries || msg.queries.length === 0) && (
                        <div className="flex items-center gap-2 px-1 py-2">
                          <div
                            className="w-3.5 h-3.5 border border-white/20 border-t-copper animate-spin"
                            style={{ borderRadius: "50%" }}
                          />
                          <span className="body-text text-sm text-white/40 italic">
                            Thinking...
                          </span>
                        </div>
                      )}

                    {/* Proposed changes */}
                    {msg.proposedChanges?.map((change) => (
                      <ProposedChangeCard
                        key={change.id}
                        change={change}
                        onConfirm={() => handleConfirmChange(msg.id, change.id)}
                        onReject={() => handleRejectChange(msg.id, change.id)}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input Area */}
      <div className="flex-shrink-0 border-t border-white/10 px-4 sm:px-6 py-4">
        <div className="max-w-3xl mx-auto">
          <div className="flex items-end gap-3">
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder="Ask about the data..."
              disabled={isStreaming}
              rows={1}
              className="flex-1 min-h-[44px] max-h-[160px] resize-none px-4 py-3 bg-navy-light border border-white/10 text-white body-text text-sm placeholder:text-white/25 focus:border-copper/60 focus:outline-none transition-colors disabled:opacity-50"
              style={{ borderRadius: "1px" }}
            />
            <button
              onClick={() => sendMessage(input)}
              disabled={isStreaming || !input.trim()}
              className="flex-shrink-0 w-11 h-11 flex items-center justify-center bg-copper text-white hover:bg-copper-dark transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              aria-label="Send message"
            >
              <Send size={16} strokeWidth={2} />
            </button>
          </div>
          <p className="body-text text-xs text-white/20 mt-2 text-center">
            Shift+Enter for new line. Changes require confirmation before executing.
          </p>
        </div>
      </div>
    </div>
  );
}

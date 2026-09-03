"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import {
  SendIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CheckIcon,
  XIcon,
  DatabaseIcon,
  AlertTriangleIcon,
  PlusIcon,
  TrashIcon,
  PanelLeftIcon,
} from "@/components/admin/AdminIcons";

/* ── Types ────────────────────────────────────────────────────────────── */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface AdminSSEEvent {
  type: "text" | "query" | "proposed_change" | "done" | "error" | "meta";
  data:
    | string
    | QueryData
    | ProposedChangeData
    | MetaData
    | Record<string, never>;
}

interface MetaData {
  conversation_id: number;
}

interface QueryData {
  sql: string;
  explanation: string;
  columns?: string[];
  rows?: (string | number | boolean | null)[][];
  total_rows?: number;
  truncated?: boolean;
  error?: string;
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
  status:
    | "pending"
    | "confirmed"
    | "rejected"
    | "executing"
    | "executed"
    | "error";
  result?: { status: string; rows_affected: number };
  error?: string;
}

interface ConversationListItem {
  id: number;
  title: string | null;
  created_at: string;
  message_count: number;
}

/* ── SSE Stream Parser ────────────────────────────────────────────────── */

async function* streamAdminChat(
  message: string,
  token: string,
  conversationId?: number | null
): AsyncGenerator<AdminSSEEvent, void, unknown> {
  const body: Record<string, unknown> = { message };
  if (conversationId) {
    body.conversation_id = conversationId;
  }

  const res = await fetch(`${API_BASE}/admin/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new Error("Unauthorized. CheckIcon your password.");
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

/* ── API Helpers ──────────────────────────────────────────────────────── */

async function executeChange(
  sql: string,
  token: string
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

async function fetchConversations(
  token: string
): Promise<ConversationListItem[]> {
  const res = await fetch(`${API_BASE}/admin/conversations`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Failed to fetch conversations: ${res.status}`);
  return res.json();
}

async function fetchConversation(
  token: string,
  id: number
): Promise<{
  id: number;
  title: string;
  messages: Array<{
    id: number;
    role: string;
    content: string | null;
    queries: QueryData[] | null;
    proposed_changes: ProposedChangeData[] | null;
    created_at: string;
  }>;
}> {
  const res = await fetch(`${API_BASE}/admin/conversations/${id}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Failed to fetch conversation: ${res.status}`);
  return res.json();
}

async function deleteConversation(
  token: string,
  id: number
): Promise<void> {
  const res = await fetch(`${API_BASE}/admin/conversations/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Failed to delete conversation: ${res.status}`);
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
    <div className="flex-1 flex items-center justify-center px-6 bg-[var(--sr-surface-page)]">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="heading-display text-3xl text-[var(--sr-text-primary)] mb-2">
            Data Admin
          </h1>
          <p className="text-[13px] text-[var(--sr-text-label)]">sailratings.com</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            ref={inputRef}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            className="w-full h-12 px-4 bg-[var(--sr-surface-card)] border border-[var(--sr-link)]/25 text-[var(--sr-text-primary)] text-[13px] placeholder:text-[var(--sr-text-label)] focus:border-[var(--sr-link)] focus:ring-1 focus:ring-[var(--sr-link)]/20 outline-none transition-all rounded-[4px] shadow-sm"
          />
          {error && <p className="text-[13px] text-[var(--sr-action-pressed)]">{error}</p>}
          <button
            type="submit"
            className="w-full h-12 bg-[var(--sr-link)] text-[var(--sr-text-primary)] text-[13px] font-medium hover:bg-[var(--sr-focus)] transition-colors rounded-[4px] shadow-sm"
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
  const [showSql, setShowSql] = useState(false);

  const hasResults =
    (query.rows?.length ?? 0) > 0 || (query.total_rows ?? 0) > 0;
  const summary =
    query.error !== undefined
      ? `error: ${query.error}`
      : query.total_rows !== undefined
        ? `${query.total_rows} row${query.total_rows === 1 ? "" : "s"}`
        : "";

  return (
    <div className="my-3 border border-[var(--sr-link)]/12 bg-[var(--sr-surface-card)] rounded-[4px] shadow-sm overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-[var(--sr-surface-interactive)] transition-colors"
      >
        <DatabaseIcon
          size={14}
          strokeWidth={1.5}
          className={`flex-shrink-0 ${query.error ? "text-[var(--sr-action-pressed)]" : "text-[var(--sr-link)]"}`}
        />
        <span className="text-[13px] text-[var(--sr-text-primary)] flex-1">
          {query.explanation || query.sql.slice(0, 80)}
        </span>
        {summary && (
          <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)] flex-shrink-0">
            {summary}
          </span>
        )}
        {expanded ? (
          <ChevronDownIcon size={14} className="text-[var(--sr-text-label)] flex-shrink-0" />
        ) : (
          <ChevronRightIcon size={14} className="text-[var(--sr-text-label)] flex-shrink-0" />
        )}
      </button>
      {expanded && (
        <div className="border-t border-[var(--sr-link)]/12">
          {hasResults && query.columns && query.rows && (
            <div className="overflow-x-auto max-h-96 overflow-y-auto">
              <table className="w-full admin-mono-font text-[10px]">
                <thead className="sticky top-0 bg-[var(--sr-surface-interactive)]/95 backdrop-blur z-10">
                  <tr>
                    {query.columns.map((c) => (
                      <th
                        key={c}
                        className="text-left px-3 py-2 text-[var(--sr-text-label)] font-medium border-b border-[var(--sr-link)]/12 whitespace-nowrap"
                      >
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {query.rows.map((row, i) => (
                    <tr
                      key={i}
                      className="border-b border-[var(--sr-link)]/5 hover:bg-[var(--sr-surface-interactive)] transition-colors"
                    >
                      {row.map((cell, j) => (
                        <td
                          key={j}
                          className="px-3 py-1.5 text-[var(--sr-text-primary)] align-top"
                          title={cell === null ? "NULL" : String(cell)}
                        >
                          {cell === null ? (
                            <span className="text-[var(--sr-text-label)] italic">NULL</span>
                          ) : typeof cell === "object" ? (
                            <span className="text-[var(--sr-link)]">
                              {JSON.stringify(cell).slice(0, 80)}
                            </span>
                          ) : (
                            String(cell).slice(0, 200)
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {query.truncated && (
                <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] px-3 py-2 border-t border-[var(--sr-link)]/12">
                  Showing first {query.rows.length} of {query.total_rows} rows
                </p>
              )}
            </div>
          )}
          {!hasResults && !query.error && (
            <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] px-4 py-3">
              0 rows
            </p>
          )}
          {query.error && (
            <p className="admin-mono-font text-[10px] text-[var(--sr-action-pressed)] px-4 py-3 whitespace-pre-wrap">
              {query.error}
            </p>
          )}
          <div className="px-4 py-2 border-t border-[var(--sr-link)]/12 bg-[var(--sr-surface-interactive)]">
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowSql(!showSql);
              }}
              className="text-[11px] text-[var(--sr-link)] hover:text-[var(--sr-focus)] transition-colors font-medium"
            >
              {showSql ? "Hide SQL" : "Show SQL"}
            </button>
            {showSql && (
              <pre className="admin-mono-font text-[10px] text-[var(--sr-text-primary)] whitespace-pre-wrap break-all mt-2 leading-relaxed bg-[var(--sr-surface-card)] p-3 border border-[var(--sr-link)]/12 rounded-[4px]">
                {query.sql}
              </pre>
            )}
          </div>
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
    <div className="my-3 border border-[var(--sr-status-warning)]/40 bg-[var(--sr-status-warning)]/12 rounded-[10px] overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--sr-status-warning)]/20 flex items-center gap-2">
        <AlertTriangleIcon
          size={14}
          strokeWidth={1.5}
          className="text-[var(--sr-status-warning)] flex-shrink-0"
        />
        <span className="admin-mono-font text-[10px] tracking-[0.14em] uppercase text-[var(--sr-status-warning)] font-semibold">
          Proposed Change
        </span>
        {change.data.affected_rows_estimate && (
          <span className="admin-mono-font text-[10px] text-[var(--sr-status-warning)]/70 ml-auto">
            ~{change.data.affected_rows_estimate} rows
          </span>
        )}
      </div>

      <div className="px-4 py-3">
        <p className="text-[13px] text-[var(--sr-text-primary)] mb-3">
          {change.data.explanation}
        </p>
        <pre className="admin-mono-font text-[10px] text-[var(--sr-status-warning)] whitespace-pre-wrap break-all bg-[var(--sr-surface-card)] px-3 py-2 rounded-[4px] leading-relaxed border border-[var(--sr-status-warning)]/20">
          {change.data.sql}
        </pre>
      </div>

      <div className="px-4 py-3 border-t border-[var(--sr-status-warning)]/20">
        {change.status === "pending" && (
          <div className="flex items-center gap-3">
            <button
              onClick={onConfirm}
              className="flex items-center gap-1.5 px-4 py-2 bg-[var(--sr-link)] text-[var(--sr-text-primary)] text-[11px] font-medium hover:bg-[var(--sr-focus)] transition-colors rounded-[4px] shadow-sm"
            >
              <CheckIcon size={14} strokeWidth={2} />
              Confirm
            </button>
            <button
              onClick={onReject}
              className="flex items-center gap-1.5 px-4 py-2 border border-[var(--sr-link)]/30 text-[var(--sr-link)] text-[11px] hover:bg-[var(--sr-surface-card)] transition-colors rounded-[4px]"
            >
              <XIcon size={14} strokeWidth={2} />
              Reject
            </button>
          </div>
        )}
        {change.status === "executing" && (
          <div className="flex items-center gap-2">
            <div
              className="w-3.5 h-3.5 border border-[var(--sr-status-warning)]/30 border-t-[var(--sr-status-warning)] animate-spin"
              style={{ borderRadius: "50%" }}
            />
            <span className="text-[11px] text-[var(--sr-status-warning)] italic">
              Executing...
            </span>
          </div>
        )}
        {change.status === "executed" && change.result && (
          <div className="flex items-center gap-2">
            <CheckIcon size={14} strokeWidth={2} className="text-[var(--sr-status-success)]" />
            <span className="text-[11px] text-[var(--sr-status-success)] font-medium">
              Executed successfully. {change.result.rows_affected} row
              {change.result.rows_affected !== 1 ? "s" : ""} affected.
            </span>
          </div>
        )}
        {change.status === "rejected" && (
          <span className="text-[11px] text-[var(--sr-status-warning)]/70 italic">
            Change rejected.
          </span>
        )}
        {change.status === "error" && (
          <div className="flex items-center gap-2">
            <XIcon size={14} strokeWidth={2} className="text-[var(--sr-action-pressed)]" />
            <span className="text-[11px] text-[var(--sr-action-pressed)] font-medium">
              {change.error || "Execution failed."}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Conversation Sidebar ────────────────────────────────────────────── */

function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  collapsed,
  onToggle,
}: {
  conversations: ConversationListItem[];
  activeId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const formatTime = (iso: string) => {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.floor(diffHr / 24);
    if (diffDay < 7) return `${diffDay}d ago`;
    return d.toLocaleDateString();
  };

  if (collapsed) {
    return (
      <div className="flex-shrink-0 border-r border-[var(--sr-link)]/12 flex flex-col items-center py-4 w-12 bg-[var(--sr-surface-interactive)]">
        <button
          onClick={onToggle}
          className="text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] transition-colors mb-4"
          title="Show conversations"
        >
          <PanelLeftIcon size={18} strokeWidth={1.5} />
        </button>
        <button
          onClick={onNew}
          className="text-[var(--sr-link)] hover:text-[var(--sr-focus)] transition-colors"
          title="New conversation"
        >
          <PlusIcon size={18} strokeWidth={2} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex-shrink-0 w-64 border-r border-[var(--sr-link)]/12 flex flex-col bg-[var(--sr-surface-interactive)]">
      {/* Sidebar header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-[var(--sr-link)]/12">
        <button
          onClick={onNew}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--sr-surface-card)] text-[var(--sr-link)] text-[13px] font-medium hover:bg-[var(--sr-surface-interactive)] transition-colors rounded-[4px]"
        >
          <PlusIcon size={14} strokeWidth={2} />
          New
        </button>
        <button
          onClick={onToggle}
          className="text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] transition-colors"
          title="Hide sidebar"
        >
          <PanelLeftIcon size={18} strokeWidth={1.5} />
        </button>
      </div>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto">
        {conversations.length === 0 && (
          <p className="text-[13px] text-[var(--sr-text-label)] px-3 py-6 text-center">
            No conversations yet
          </p>
        )}
        {conversations.map((conv) => (
          <div
            key={conv.id}
            className={`group flex items-start gap-2 px-3 py-2.5 cursor-pointer border-b border-[var(--sr-link)]/5 transition-colors ${
              conv.id === activeId
                ? "bg-[var(--sr-surface-card)] border-l-2 border-l-[var(--sr-link)]"
                : "hover:bg-[var(--sr-surface-interactive)] border-l-2 border-l-transparent"
            }`}
            onClick={() => onSelect(conv.id)}
          >
            <div className="flex-1 min-w-0">
              <p
                className={`text-[13px] truncate ${
                  conv.id === activeId ? "text-[var(--sr-text-primary)] font-medium" : "text-[var(--sr-text-tertiary)]"
                }`}
              >
                <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)] mr-1.5">#{conv.id}</span>
                {conv.title || "Untitled"}
              </p>
              <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] mt-0.5">
                {formatTime(conv.created_at)}
              </p>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDelete(conv.id);
              }}
              className="opacity-0 group-hover:opacity-100 text-[var(--sr-text-label)] hover:text-[var(--sr-action-pressed)] transition-all flex-shrink-0 mt-0.5"
              title="Delete conversation"
            >
              <TrashIcon size={13} strokeWidth={1.5} />
            </button>
          </div>
        ))}
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
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [conversations, setConversations] = useState<ConversationListItem[]>(
    []
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);

  // Load token from localStorage or environment on mount
  useEffect(() => {
    const stored = localStorage.getItem("admin_token") || process.env.NEXT_PUBLIC_ADMIN_PASSWORD || "sailfast2026";
    if (stored) {
      localStorage.setItem("admin_token", stored);
      setToken(stored);
    }
  }, []);

  // Load conversations when token is set
  useEffect(() => {
    if (!token) return;
    fetchConversations(token)
      .then(setConversations)
      .catch(() => {});
  }, [token]);

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

  const refreshConversations = useCallback(() => {
    if (!token) return;
    fetchConversations(token)
      .then(setConversations)
      .catch(() => {});
  }, [token]);

  const handleLogin = useCallback((password: string) => {
    localStorage.setItem("admin_token", password);
    setToken(password);
  }, []);

  const handleLogout = useCallback(() => {
    localStorage.removeItem("admin_token");
    setToken(null);
    setMessages([]);
    setConversationId(null);
    setConversations([]);
  }, []);

  const handleNewConversation = useCallback(() => {
    setMessages([]);
    setConversationId(null);
    inputRef.current?.focus();
  }, []);

  const handleSelectConversation = useCallback(
    async (id: number) => {
      if (!token || id === conversationId) return;

      try {
        const data = await fetchConversation(token, id);
        const loaded: ChatMessage[] = data.messages.map((m, idx) => {
          if (m.role === "user") {
            return {
              id: `loaded-user-${m.id}`,
              role: "user" as const,
              content: m.content || "",
            };
          }
          return {
            id: `loaded-assistant-${m.id}`,
            role: "assistant" as const,
            content: m.content || "",
            queries: m.queries || undefined,
            proposedChanges: m.proposed_changes
              ? (m.proposed_changes as unknown as Array<Record<string, unknown>>).map(
                  (c, ci) => ({
                    id: `loaded-change-${m.id}-${ci}`,
                    data: {
                      sql: (c.sql as string) || "",
                      explanation: (c.explanation as string) || "",
                      affected_rows_estimate:
                        (c.affected_rows_estimate as string) || "",
                    },
                    status: ((c.status as string) || "pending") as ProposedChangeItem["status"],
                    result: c.result as
                      | { status: string; rows_affected: number }
                      | undefined,
                  })
                )
              : undefined,
          };
        });

        setConversationId(id);
        setMessages(loaded);
      } catch (err) {
        console.error("Failed to load conversation:", err);
      }
    },
    [token, conversationId]
  );

  const handleDeleteConversation = useCallback(
    async (id: number) => {
      if (!token) return;
      try {
        await deleteConversation(token, id);
        if (conversationId === id) {
          setMessages([]);
          setConversationId(null);
        }
        refreshConversations();
      } catch (err) {
        console.error("Failed to delete conversation:", err);
      }
    },
    [token, conversationId, refreshConversations]
  );

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
        const stream = streamAdminChat(text.trim(), token, conversationId);
        for await (const event of stream) {
          if (event.type === "meta") {
            const meta = event.data as MetaData;
            setConversationId(meta.conversation_id);
          } else if (event.type === "text") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + (event.data as string) }
                  : m
              )
            );
          } else if (event.type === "query") {
            const queryData = event.data as QueryData;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, queries: [...(m.queries || []), queryData] }
                  : m
              )
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
                      proposedChanges: [
                        ...(m.proposedChanges || []),
                        changeItem,
                      ],
                    }
                  : m
              )
            );
          } else if (event.type === "done") {
            break;
          } else if (event.type === "error") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content:
                        m.content +
                        "\n\n[Error: " +
                        (event.data as string) +
                        "]",
                    }
                  : m
              )
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
              : m
          )
        );

        // If unauthorized, clear token
        if (errorText.includes("Unauthorized")) {
          localStorage.removeItem("admin_token");
          setToken(null);
        }
      } finally {
        setIsStreaming(false);
        refreshConversations();
      }
    },
    [token, isStreaming, conversationId, refreshConversations]
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
                  c.id === changeId
                    ? { ...c, status: "executing" as const }
                    : c
                ),
              }
            : m
        )
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
                      : c
                  ),
                }
              : m
          )
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
                      : c
                  ),
                }
              : m
          )
        );
      }
    },
    [token, messages]
  );

  const handleRejectChange = useCallback(
    (messageId: string, changeId: string) => {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                proposedChanges: m.proposedChanges?.map((c) =>
                  c.id === changeId
                    ? { ...c, status: "rejected" as const }
                    : c
                ),
              }
            : m
        )
      );
    },
    []
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
    <>

      {/* Main content: sidebar + chat */}
      <div className="flex-1 flex overflow-hidden">
        {/* Conversation sidebar */}
        <ConversationSidebar
          conversations={conversations}
          activeId={conversationId}
          onSelect={handleSelectConversation}
          onNew={handleNewConversation}
          onDelete={handleDeleteConversation}
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
        />

        {/* Chat area */}
        <div className="flex-1 flex flex-col min-w-0 bg-[var(--sr-surface-page)]">
          {/* Chat Messages */}
          <div
            ref={chatContainerRef}
            className="flex-1 overflow-y-auto px-4 sm:px-6 py-6"
          >
            <div className="max-w-3xl mx-auto space-y-4">
              {/* Empty state with examples */}
              {messages.length === 0 && (
                <div className="py-16 text-center">
                  <DatabaseIcon
                    size={32}
                    strokeWidth={1}
                    className="text-[var(--sr-text-label)]/40 mx-auto mb-6"
                  />
                  <h2 className="heading-display text-2xl text-[var(--sr-text-primary)] mb-2">
                    Ask me about the data
                  </h2>
                  <p className="text-[13px] text-[var(--sr-text-tertiary)] mb-10 max-w-md mx-auto">
                    I can query the database, investigate data quality issues,
                    and propose fixes. Ask me anything about boats,
                    certificates, or race results.
                  </p>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-lg mx-auto">
                    {EXAMPLE_PROMPTS.map((prompt) => (
                      <button
                        key={prompt}
                        onClick={() => sendMessage(prompt)}
                        className="text-left px-4 py-3 bg-[var(--sr-surface-card)] border border-[var(--sr-link)]/12 hover:bg-[var(--sr-surface-interactive)] hover:border-[var(--sr-link)]/25 transition-colors rounded-[4px] shadow-sm"
                      >
                        <span className="text-[13px] text-[var(--sr-text-primary)]">
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
                        ? "bg-[var(--sr-link)]/10 border border-[var(--sr-link)]/20 px-4 py-3"
                        : "w-full max-w-none sm:max-w-[75%]"
                    } rounded-[4px]`}
                  >
                    {msg.role === "user" ? (
                      <p className="text-[13px] text-[var(--sr-text-primary)] whitespace-pre-wrap">
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
                          <div className="text-[13px] text-[var(--sr-text-primary)] whitespace-pre-wrap leading-relaxed px-1 py-1">
                            {msg.content}
                            {isStreaming &&
                              msg.id ===
                                messages[messages.length - 1]?.id && (
                                <span className="inline-block w-0.5 h-3.5 bg-[var(--sr-link)] ml-0.5 align-text-bottom streaming-pulse" />
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
                                className="w-3.5 h-3.5 border border-[var(--sr-link)]/20 border-t-[var(--sr-link)] animate-spin"
                                style={{ borderRadius: "50%" }}
                              />
                              <span className="text-[13px] text-[var(--sr-text-label)] italic">
                                Thinking...
                              </span>
                            </div>
                          )}

                        {/* Proposed changes */}
                        {msg.proposedChanges?.map((change) => (
                          <ProposedChangeCard
                            key={change.id}
                            change={change}
                            onConfirm={() =>
                              handleConfirmChange(msg.id, change.id)
                            }
                            onReject={() =>
                              handleRejectChange(msg.id, change.id)
                            }
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
          <div className="flex-shrink-0 border-t border-[var(--sr-link)]/12 px-4 sm:px-6 py-4 bg-[var(--sr-surface-page)]">
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
                  className="flex-1 min-h-[44px] max-h-[160px] resize-none px-4 py-3 bg-[var(--sr-surface-card)] border border-[var(--sr-link)]/25 text-[var(--sr-text-primary)] text-[13px] placeholder:text-[var(--sr-text-label)] focus:border-[var(--sr-link)] focus:ring-1 focus:ring-[var(--sr-link)]/20 outline-none transition-all disabled:opacity-50 rounded-[4px] shadow-sm"
                />
                <button
                  onClick={() => sendMessage(input)}
                  disabled={isStreaming || !input.trim()}
                  className="flex-shrink-0 w-11 h-11 flex items-center justify-center bg-[var(--sr-link)] text-[var(--sr-text-primary)] hover:bg-[var(--sr-focus)] transition-colors disabled:opacity-30 disabled:cursor-not-allowed rounded-[4px] shadow-sm"
                  aria-label="SendIcon message"
                >
                  <SendIcon size={16} strokeWidth={2} />
                </button>
              </div>
              <p className="admin-mono-font text-[9px] tracking-[0.16em] uppercase text-[var(--sr-text-label)] mt-3 text-center">
                Shift+Enter for new line. Changes require confirmation before
                executing.
              </p>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

"use client";

import { useState, useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";
import { streamInsights } from "@/lib/api";

interface TeaserAnalysisProps {
  boatId: number;
  boatName: string;
  onComplete?: (text: string) => void;
}

export default function TeaserAnalysis({
  boatId,
  boatName,
  onComplete,
}: TeaserAnalysisProps) {
  const [text, setText] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isDone, setIsDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const startedRef = useRef(false);
  const textRef = useRef("");

  useEffect(() => {
    // Prevent double-fire in strict mode
    if (startedRef.current) return;
    startedRef.current = true;

    let cancelled = false;

    async function run() {
      setIsStreaming(true);
      setError(null);
      setText("");
      textRef.current = "";

      try {
        const stream = streamInsights(boatId, "free");
        for await (const event of stream) {
          if (cancelled) break;

          if (event.type === "text") {
            textRef.current += event.data;
            setText(textRef.current);
          } else if (event.type === "done") {
            break;
          } else if (event.type === "error") {
            setError(event.data || "Analysis failed.");
            break;
          }
        }
      } catch {
        if (!cancelled) {
          setError("Failed to load analysis. Please try again.");
        }
      } finally {
        if (!cancelled) {
          setIsStreaming(false);
          setIsDone(true);
          if (textRef.current && onComplete) {
            onComplete(textRef.current);
          }
        }
      }
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [boatId, onComplete]);

  if (error && !text) {
    return (
      <div className="w-full max-w-2xl mx-auto border border-border bg-white p-8">
        <p className="body-text text-brass">{error}</p>
      </div>
    );
  }

  return (
    <div className="w-full max-w-2xl mx-auto">
      {/* Document-style header */}
      <div className="border border-border bg-white">
        {/* Top edge line */}
        <div className="border-b border-border-light px-8 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <Sparkles
              size={16}
              strokeWidth={1.5}
              className={`text-brass ${isStreaming ? "streaming-pulse" : ""}`}
            />
            <span className="text-xs uppercase tracking-widest text-muted font-body">
              Rating Analysis
            </span>
          </div>
          <span className="text-xs text-muted data-mono">
            {boatName}
          </span>
        </div>

        {/* Analysis text body */}
        <div className="relative px-8 py-8 sm:px-12 sm:py-10">
          <div className={`teaser-fade ${isDone ? "" : ""}`}>
            <div className="body-text text-charcoal-light text-base sm:text-lg leading-relaxed whitespace-pre-wrap">
              {text}
              {isStreaming && (
                <span className="inline-block w-0.5 h-5 bg-brass ml-0.5 align-text-bottom streaming-pulse" />
              )}
            </div>
          </div>

          {/* Loading state when no text yet */}
          {isStreaming && !text && (
            <div className="flex items-center gap-3 py-4">
              <div
                className="w-4 h-4 border border-border border-t-brass animate-spin"
                style={{ borderRadius: "50%" }}
              />
              <span className="body-text text-muted italic">
                Analysing rating data...
              </span>
            </div>
          )}
        </div>

        {/* Bottom edge */}
        {isDone && (
          <div className="border-t border-border-light px-8 py-3 flex items-center justify-between">
            <span className="text-xs text-muted/60 font-body italic">
              You&rsquo;re seeing the surface. The full report goes deeper.
            </span>
            <span className="text-xs text-muted/40 data-mono">
              sailratings.com
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

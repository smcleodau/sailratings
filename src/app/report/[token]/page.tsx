"use client";

import { useState, useEffect, useRef, use } from "react";
import { getReport, type ReportData } from "@/lib/api";
import ReportView from "@/components/ReportView";
import Link from "next/link";

export default function ReportPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [report, setReport] = useState<ReportData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);

  useEffect(() => {
    let cancelled = false;

    async function fetchReport() {
      try {
        const data = await getReport(token);
        if (cancelled) return;

        setReport(data);

        if (data.status === "ready" || data.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch {
        if (!cancelled) {
          setError("Failed to load your report. Please try again later.");
          if (pollRef.current) clearInterval(pollRef.current);
        }
      }
    }

    fetchReport();
    pollRef.current = setInterval(fetchReport, 3000);

    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [token]);

  // Loading / pending / paid states
  if (error) {
    return (
      <main className="min-h-screen bg-cream flex items-center justify-center px-6">
        <div className="max-w-md text-center">
          <h1 className="heading-display text-3xl text-ink mb-4">
            Something went wrong
          </h1>
          <p className="body-text text-muted mb-8">{error}</p>
          <Link
            href="/"
            className="inline-block bg-brass text-white px-6 py-3 font-body font-medium hover:bg-brass-dark transition-colors"
            style={{ borderRadius: "1px" }}
          >
            Back to Home
          </Link>
        </div>
      </main>
    );
  }

  if (!report || report.status === "pending" || report.status === "paid") {
    return (
      <main className="min-h-screen bg-cream flex items-center justify-center px-6">
        <div className="max-w-md text-center">
          <div className="mb-8">
            <span className="brand-wordmark text-sm text-navy/40">
              Sail Ratings
            </span>
          </div>
          <div className="flex justify-center mb-6">
            <div
              className="w-8 h-8 border-2 border-border border-t-brass animate-spin"
              style={{ borderRadius: "50%" }}
            />
          </div>
          <h1 className="heading-display text-2xl text-ink mb-3">
            Preparing Your Report
          </h1>
          <p className="body-text text-muted text-base">
            {report?.status === "paid"
              ? "Payment confirmed. Generating your analysis..."
              : "Processing your payment..."}
          </p>
        </div>
      </main>
    );
  }

  if (report.status === "error") {
    return (
      <main className="min-h-screen bg-cream flex items-center justify-center px-6">
        <div className="max-w-md text-center">
          <h1 className="heading-display text-3xl text-ink mb-4">
            Report Error
          </h1>
          <p className="body-text text-muted mb-8">
            There was a problem generating your report. Please contact us for
            assistance.
          </p>
          <Link
            href="/"
            className="inline-block bg-brass text-white px-6 py-3 font-body font-medium hover:bg-brass-dark transition-colors"
            style={{ borderRadius: "1px" }}
          >
            Back to Home
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-cream">
      {/* Nav */}
      <nav className="border-b border-border-light px-8 py-5 flex items-center justify-between">
        <Link href="/">
          <span className="brand-wordmark text-sm text-navy">
            Sail Ratings
          </span>
        </Link>
      </nav>

      <ReportView report={report} token={token} />

      {/* Footer */}
      <footer className="border-t border-border-light px-6 py-10">
        <div className="max-w-3xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-muted">
          <span className="brand-wordmark text-xs text-muted/60">
            Sail Ratings
          </span>
          <span className="body-text text-center">
            IRC rating data sourced from public certificates. Not affiliated
            with the RORC Rating Office.
          </span>
        </div>
      </footer>
    </main>
  );
}

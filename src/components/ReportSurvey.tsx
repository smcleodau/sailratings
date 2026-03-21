"use client";

import { useState } from "react";
import { submitSurvey } from "@/lib/api";

interface ReportSurveyProps {
  orderToken: string;
}

export default function ReportSurvey({ orderToken }: ReportSurveyProps) {
  const [score, setScore] = useState<number | null>(null);
  const [newsletter, setNewsletter] = useState(false);
  const [userType, setUserType] = useState<string | null>(null);
  const [missingInfo, setMissingInfo] = useState("");
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      await submitSurvey({
        order_token: orderToken,
        usefulness_score: score,
        newsletter_signup: newsletter,
        user_type: userType,
        missing_info: missingInfo || undefined,
        email: email || undefined,
      });
      setSubmitted(true);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to submit";
      if (msg.includes("409")) {
        setSubmitted(true);
      } else {
        setError(msg);
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <section className="max-w-3xl mx-auto px-6 pb-12">
        <div className="border border-border bg-white px-8 py-10 text-center">
          <h3 className="heading-display text-2xl text-ink mb-3">
            Thanks for your feedback
          </h3>
          <p className="body-text text-muted">
            Your input helps us build a better product for the sailing community.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="max-w-3xl mx-auto px-6 pb-12">
      <div className="border border-border bg-white px-8 py-10 space-y-8">
        <div>
          <h3 className="heading-display text-2xl text-ink mb-2">
            How was this report?
          </h3>
          <p className="body-text text-muted text-sm">
            Takes 30 seconds. Helps us improve.
          </p>
        </div>

        {/* Q1: Usefulness score */}
        <div>
          <label className="body-text text-sm font-medium text-ink block mb-3">
            How useful was this report? (1 = not at all, 10 = extremely)
          </label>
          <div className="flex gap-2 flex-wrap">
            {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => setScore(n)}
                className={`w-10 h-10 text-sm font-medium border transition-colors ${
                  score === n
                    ? "bg-brass text-white border-brass"
                    : "bg-cream text-muted border-border hover:border-brass/50"
                }`}
                style={{ borderRadius: "1px" }}
              >
                {n}
              </button>
            ))}
          </div>
        </div>

        {/* Q2: User type */}
        <div>
          <label className="body-text text-sm font-medium text-ink block mb-3">
            How would you describe yourself?
          </label>
          <div className="flex gap-3 flex-wrap">
            {[
              { value: "racer", label: "Club racer" },
              { value: "owner", label: "Boat owner" },
              { value: "pro", label: "Professional / trade" },
              { value: "other", label: "Other" },
            ].map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setUserType(opt.value)}
                className={`px-4 py-2 text-sm border transition-colors ${
                  userType === opt.value
                    ? "bg-brass text-white border-brass"
                    : "bg-cream text-muted border-border hover:border-brass/50"
                }`}
                style={{ borderRadius: "1px" }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Q3: Missing info */}
        <div>
          <label className="body-text text-sm font-medium text-ink block mb-2">
            What was missing or could be improved?
          </label>
          <textarea
            value={missingInfo}
            onChange={(e) => setMissingInfo(e.target.value)}
            placeholder="Optional — any feedback helps"
            rows={3}
            className="w-full border border-border bg-cream px-4 py-3 body-text text-sm text-ink placeholder:text-muted/50 focus:outline-none focus:border-brass"
            style={{ borderRadius: "1px" }}
          />
        </div>

        {/* Q4: Newsletter */}
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            id="newsletter"
            checked={newsletter}
            onChange={(e) => setNewsletter(e.target.checked)}
            className="mt-1 accent-[var(--brass)]"
          />
          <div>
            <label
              htmlFor="newsletter"
              className="body-text text-sm font-medium text-ink cursor-pointer"
            >
              Keep me posted on new features and racing insights
            </label>
            {newsletter && (
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
                className="mt-2 w-full max-w-xs border border-border bg-cream px-3 py-2 body-text text-sm text-ink placeholder:text-muted/50 focus:outline-none focus:border-brass"
                style={{ borderRadius: "1px" }}
              />
            )}
          </div>
        </div>

        {error && (
          <p className="body-text text-sm text-red-600">{error}</p>
        )}

        <button
          type="button"
          onClick={handleSubmit}
          disabled={submitting}
          className="bg-brass text-white px-8 py-3 font-body font-medium hover:bg-brass-dark transition-colors disabled:opacity-50"
          style={{ borderRadius: "1px" }}
        >
          {submitting ? "Submitting..." : "Submit Feedback"}
        </button>
      </div>
    </section>
  );
}

"use client";

import { useState, useEffect } from "react";
import Image from "next/image";

/* ─────────────────────────────────────────────────────────
   Brand Option A — "Classic Maritime"
   Standalone preview page — zero external component imports
   ───────────────────────────────────────────────────────── */

// ── Brand tokens ──────────────────────────────────────────
const C = {
  primary:    "#0A2240",
  secondary:  "#C29B61",
  accent:     "#A31621",
  bg:         "#F4F1E8",
  text:       "#0A2240",
  muted:      "#6B7A8F",
  border:     "#D1C8B7",
  white:      "#FFFFFF",
} as const;

// ── Cycling "IRC / ORC" in the headline ───────────────────
const SYSTEMS = ["IRC", "ORC"];
const INTERVAL = 3400;

function CyclingSystem() {
  const [idx, setIdx] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const timer = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setIdx((prev) => (prev + 1) % SYSTEMS.length);
        setVisible(true);
      }, 340);
    }, INTERVAL);
    return () => clearInterval(timer);
  }, []);

  return (
    <span
      style={{
        display: "inline-block",
        color: C.secondary,
        opacity: visible ? 1 : 0,
        transform: visible ? "translateY(0)" : "translateY(6px)",
        transition: "opacity 0.35s ease, transform 0.35s ease",
      }}
    >
      {SYSTEMS[idx]}
    </span>
  );
}

// ── Compass‑rose SVG logo mark ────────────────────────────
function CompassRose({ size = 40, color = C.secondary }: { size?: number; color?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      {/* Outer ring */}
      <circle cx="50" cy="50" r="46" stroke={color} strokeWidth="2" />
      <circle cx="50" cy="50" r="42" stroke={color} strokeWidth="0.5" />
      {/* Cardinal points — N / S / E / W */}
      <polygon points="50,6 45,38 55,38"  fill={color} />
      <polygon points="50,94 45,62 55,62"  fill={color} opacity="0.5" />
      <polygon points="6,50 38,45 38,55"   fill={color} opacity="0.5" />
      <polygon points="94,50 62,45 62,55"   fill={color} opacity="0.5" />
      {/* Intercardinal points */}
      <polygon points="18,18 40,42 42,40"  fill={color} opacity="0.35" />
      <polygon points="82,18 60,42 58,40"  fill={color} opacity="0.35" />
      <polygon points="18,82 40,58 42,60"  fill={color} opacity="0.35" />
      <polygon points="82,82 60,58 58,60"  fill={color} opacity="0.35" />
      {/* Centre pip */}
      <circle cx="50" cy="50" r="3" fill={color} />
    </svg>
  );
}

// ── Star rating icons ─────────────────────────────────────
function Stars({ count }: { count: number }) {
  return (
    <span style={{ color: C.secondary, fontSize: 14, letterSpacing: 2 }}>
      {"★".repeat(count)}
      {"☆".repeat(5 - count)}
    </span>
  );
}

// ── Decorative divider ────────────────────────────────────
function Divider() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        padding: "48px 0 0",
      }}
    >
      <span style={{ flex: 1, maxWidth: 120, height: 1, background: C.border }} />
      <CompassRose size={20} color={C.border} />
      <span style={{ flex: 1, maxWidth: 120, height: 1, background: C.border }} />
    </div>
  );
}

// ── Swatch block for palette ──────────────────────────────
function Swatch({ hex, label, name }: { hex: string; label: string; name: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
      <div
        style={{
          width: 72,
          height: 72,
          borderRadius: 12,
          background: hex,
          border: hex === C.bg ? `1px solid ${C.border}` : "none",
          boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
        }}
      />
      <span style={{ fontFamily: "'Source Sans 3', sans-serif", fontSize: 13, fontWeight: 600, color: C.text }}>
        {name}
      </span>
      <span style={{ fontFamily: "'Source Code Pro', monospace", fontSize: 11, color: C.muted }}>
        {hex}
      </span>
      <span style={{ fontFamily: "'Source Sans 3', sans-serif", fontSize: 11, color: C.muted }}>
        {label}
      </span>
    </div>
  );
}

// ── SearchIcon ────────────────────────────────────────────
function SearchIcon({ color = C.muted, size = 20 }: { color?: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}

// ══════════════════════════════════════════════════════════
//  PAGE
// ══════════════════════════════════════════════════════════
export default function BrandAPage() {

  return (
    <>
      {/* ── Google Fonts ── */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@400;700;900&family=Source+Sans+3:ital,wght@0,300;0,400;0,600;0,700;1,400&family=Source+Code+Pro:wght@400;500&display=swap');

        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        html { scroll-behavior: smooth; }

        body {
          background: ${C.bg};
          color: ${C.text};
          -webkit-font-smoothing: antialiased;
          -moz-osx-font-smoothing: grayscale;
        }

        /* Entrance animations */
        @keyframes brandFadeUp {
          from { opacity: 0; transform: translateY(18px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .brand-a-anim-1 { animation: brandFadeUp 0.8s ease both 0.2s; }
        .brand-a-anim-2 { animation: brandFadeUp 0.8s ease both 0.45s; }
        .brand-a-anim-3 { animation: brandFadeUp 0.8s ease both 0.65s; }
        .brand-a-anim-4 { animation: brandFadeUp 0.8s ease both 0.85s; }
        .brand-a-anim-5 { animation: brandFadeUp 0.8s ease both 1.05s; }

        /* Hero image treatment */
        .brand-a-hero-img {
          filter: saturate(0.35) contrast(1.05) brightness(0.85);
        }

        /* Search bar focus ring */
        .brand-a-search:focus-within {
          box-shadow: 0 0 0 3px ${C.secondary}44, 0 8px 32px rgba(0,0,0,0.12);
        }

        /* CTA hover */
        .brand-a-cta-primary:hover {
          background: ${C.secondary} !important;
          color: ${C.white} !important;
          transform: translateY(-1px);
          box-shadow: 0 6px 20px rgba(194,155,97,0.35);
        }

        .brand-a-cta-secondary:hover {
          background: ${C.primary} !important;
          color: ${C.white} !important;
          transform: translateY(-1px);
        }

        /* Card hover */
        .brand-a-card:hover {
          transform: translateY(-3px);
          box-shadow: 0 12px 40px rgba(10,34,64,0.12);
        }

        /* Smooth transitions on interactive */
        .brand-a-cta-primary,
        .brand-a-cta-secondary,
        .brand-a-card {
          transition: all 0.25s ease;
        }

        /* Scrollbar styling */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: ${C.bg}; }
        ::-webkit-scrollbar-thumb { background: ${C.border}; border-radius: 4px; }
      `}</style>

      <div style={{ fontFamily: "'Source Sans 3', sans-serif", background: C.bg, minHeight: "100vh" }}>

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            OPTION BAR
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <div
          style={{
            position: "sticky",
            top: 0,
            zIndex: 100,
            background: C.primary,
            color: C.white,
            fontFamily: "'Source Sans 3', sans-serif",
            fontSize: 13,
            fontWeight: 600,
            letterSpacing: "0.04em",
            padding: "10px 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottom: `2px solid ${C.secondary}`,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
                background: C.secondary,
                color: C.primary,
                fontSize: 11,
                fontWeight: 700,
                padding: "2px 10px",
                borderRadius: 3,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
              }}
            >
              Option A
            </span>
            <span>Classic Maritime</span>
          </div>
          <a
            href="/brand"
            style={{
              color: C.secondary,
              textDecoration: "none",
              fontSize: 13,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span style={{ fontSize: 18, lineHeight: 1 }}>&larr;</span>
            All Options
          </a>
        </div>

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            NAVIGATION
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <nav
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 clamp(20px, 4vw, 48px)",
            height: 72,
            background: C.white,
            borderBottom: `1px solid ${C.border}`,
            position: "relative",
            zIndex: 50,
          }}
        >
          {/* Logo lockup */}
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <CompassRose size={36} color={C.primary} />
            <span
              style={{
                fontFamily: "'Cinzel Decorative', cursive",
                fontWeight: 700,
                fontSize: 18,
                letterSpacing: "0.1em",
                color: C.primary,
              }}
            >
              SAIL RATINGS
            </span>
          </div>

          {/* Nav links */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 32,
              fontSize: 14,
              fontWeight: 600,
              color: C.muted,
            }}
          >
            <span style={{ color: C.text, cursor: "pointer" }}>Ratings</span>
            <span style={{ cursor: "pointer" }}>Fleet&nbsp;Analysis</span>
            <span style={{ cursor: "pointer" }}>Results</span>
            <span style={{ cursor: "pointer" }}>About</span>
            <span
              style={{
                background: C.primary,
                color: C.white,
                padding: "8px 20px",
                borderRadius: 4,
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: "0.03em",
                cursor: "pointer",
              }}
            >
              Sign In
            </span>
          </div>
        </nav>

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            HERO SECTION
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <section
          style={{
            position: "relative",
            height: "85vh",
            minHeight: 560,
            overflow: "hidden",
          }}
        >
          {/* Background image */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              zIndex: 0,
            }}
          >
            <Image
              src="/hero.jpg"
              alt="Racing yachts under sail"
              fill
              priority
              className="brand-a-hero-img"
              style={{ objectFit: "cover", objectPosition: "center" }}
              sizes="100vw"
              quality={85}
            />
            {/* Deep navy-tinted overlay */}
            <div
              style={{
                position: "absolute",
                inset: 0,
                background: `linear-gradient(
                  180deg,
                  ${C.primary}D9 0%,
                  ${C.primary}99 35%,
                  ${C.primary}B3 70%,
                  ${C.primary}F2 100%
                )`,
                zIndex: 1,
              }}
            />
            {/* Warm vintage wash */}
            <div
              style={{
                position: "absolute",
                inset: 0,
                background: `linear-gradient(135deg, ${C.secondary}15 0%, transparent 60%)`,
                zIndex: 2,
              }}
            />
          </div>

          {/* Hero content */}
          <div
            style={{
              position: "relative",
              zIndex: 10,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              padding: "0 24px",
              textAlign: "center",
            }}
          >
            {/* Decorative top accent */}
            <div className="brand-a-anim-1" style={{ marginBottom: 28 }}>
              <CompassRose size={48} color={C.secondary} />
            </div>

            {/* Pre-headline kicker */}
            <p
              className="brand-a-anim-2"
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: "0.2em",
                textTransform: "uppercase",
                color: C.secondary,
                marginBottom: 20,
              }}
            >
              Yacht Racing Rating Analysis
            </p>

            {/* Main headline */}
            <h1
              className="brand-a-anim-2"
              style={{
                fontFamily: "'Cinzel Decorative', cursive",
                fontWeight: 700,
                fontSize: "clamp(2.5rem, 5vw, 4.5rem)",
                lineHeight: 1.15,
                letterSpacing: "0.02em",
                color: C.white,
                maxWidth: "16ch",
                textShadow: "0 2px 40px rgba(0,0,0,0.4)",
              }}
            >
              The timeless pursuit of the unseen advantage
            </h1>

            {/* Sub-headline */}
            <p
              className="brand-a-anim-3"
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: "clamp(1rem, 1.4vw, 1.2rem)",
                fontWeight: 300,
                lineHeight: 1.7,
                color: "rgba(255,255,255,0.7)",
                maxWidth: 520,
                marginTop: 24,
                textShadow: "0 1px 12px rgba(0,0,0,0.15)",
              }}
            >
              We analyze 31,000 race results and every certificate ever published
              to reveal where your <CyclingSystem /> rating points are hiding.
            </p>

            {/* Hero search bar */}
            <div
              className="brand-a-anim-4"
              style={{
                width: "100%",
                maxWidth: 580,
                marginTop: 40,
              }}
            >
              <div
                className="brand-a-search"
                style={{
                  position: "relative",
                  background: "rgba(255,255,255,0.95)",
                  backdropFilter: "blur(12px)",
                  borderRadius: 48,
                  boxShadow: "0 4px 24px rgba(0,0,0,0.15)",
                  border: `1px solid rgba(255,255,255,0.8)`,
                  overflow: "hidden",
                  display: "flex",
                  alignItems: "center",
                  transition: "box-shadow 0.3s ease",
                }}
              >
                <div style={{ paddingLeft: 22, display: "flex", alignItems: "center" }}>
                  <SearchIcon color={C.muted} size={20} />
                </div>
                <input
                  type="text"
                  readOnly
                  tabIndex={-1}
                  placeholder="Enter your boat name or sail number..."
                  style={{
                    flex: 1,
                    height: 56,
                    border: "none",
                    outline: "none",
                    background: "transparent",
                    fontFamily: "'Source Sans 3', sans-serif",
                    fontSize: 15,
                    color: C.text,
                    paddingLeft: 14,
                    paddingRight: 14,
                  }}
                />
                <button
                  style={{
                    background: C.secondary,
                    color: C.white,
                    fontFamily: "'Source Sans 3', sans-serif",
                    fontSize: 14,
                    fontWeight: 700,
                    letterSpacing: "0.04em",
                    border: "none",
                    borderRadius: 48,
                    padding: "10px 28px",
                    marginRight: 6,
                    cursor: "pointer",
                    whiteSpace: "nowrap",
                    transition: "background 0.2s ease",
                  }}
                >
                  Analyze
                </button>
              </div>

              <p
                className="brand-a-anim-5"
                style={{
                  fontSize: 12,
                  color: "rgba(255,255,255,0.4)",
                  letterSpacing: "0.06em",
                  marginTop: 14,
                  fontWeight: 400,
                }}
              >
                6,197 boats analyzed &middot; 31,000 race results &middot; 14 years of data
              </p>
            </div>
          </div>

          {/* Bottom fade to cream */}
          <div
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              right: 0,
              height: 140,
              background: `linear-gradient(to bottom, transparent, ${C.bg})`,
              zIndex: 15,
            }}
          />
        </section>

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            TRUST BAR
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <section
          style={{
            padding: "48px 24px",
            display: "flex",
            justifyContent: "center",
            gap: "clamp(24px, 5vw, 64px)",
            flexWrap: "wrap",
          }}
        >
          {[
            { num: "6,197", label: "Boats Analyzed" },
            { num: "31,000+", label: "Race Results" },
            { num: "14", label: "Years of Data" },
            { num: "98%", label: "Certificate Coverage" },
          ].map((s) => (
            <div key={s.label} style={{ textAlign: "center" }}>
              <div
                style={{
                  fontFamily: "'Cinzel Decorative', cursive",
                  fontWeight: 700,
                  fontSize: "clamp(1.6rem, 3vw, 2.2rem)",
                  color: C.primary,
                }}
              >
                {s.num}
              </div>
              <div
                style={{
                  fontFamily: "'Source Sans 3', sans-serif",
                  fontSize: 13,
                  fontWeight: 600,
                  color: C.muted,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  marginTop: 4,
                }}
              >
                {s.label}
              </div>
            </div>
          ))}
        </section>

        <Divider />

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            BOAT CARD PREVIEW
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <section style={{ padding: "56px 24px 0", maxWidth: 960, margin: "0 auto" }}>
          <div style={{ textAlign: "center", marginBottom: 40 }}>
            <p
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: "0.18em",
                textTransform: "uppercase",
                color: C.secondary,
                marginBottom: 12,
              }}
            >
              Rating Intelligence
            </p>
            <h2
              style={{
                fontFamily: "'Cinzel Decorative', cursive",
                fontWeight: 700,
                fontSize: "clamp(1.5rem, 3vw, 2.2rem)",
                color: C.primary,
                lineHeight: 1.25,
              }}
            >
              Deep Boat Analysis
            </h2>
            <p
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 16,
                color: C.muted,
                lineHeight: 1.65,
                maxWidth: 500,
                margin: "14px auto 0",
              }}
            >
              Every certificate dissected. Every competitor compared.
              Your rating, fully understood.
            </p>
          </div>

          {/* Boat card */}
          <div
            className="brand-a-card"
            style={{
              background: C.white,
              borderRadius: 12,
              border: `1px solid ${C.border}`,
              overflow: "hidden",
              maxWidth: 680,
              margin: "0 auto",
              cursor: "pointer",
            }}
          >
            {/* Card header band */}
            <div
              style={{
                background: C.primary,
                padding: "18px 28px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <div>
                <h3
                  style={{
                    fontFamily: "'Cinzel Decorative', cursive",
                    fontWeight: 700,
                    fontSize: 20,
                    color: C.white,
                    letterSpacing: "0.04em",
                  }}
                >
                  ALCHEMY
                </h3>
                <p
                  style={{
                    fontFamily: "'Source Sans 3', sans-serif",
                    fontSize: 13,
                    color: "rgba(255,255,255,0.55)",
                    marginTop: 2,
                  }}
                >
                  J/112E &middot; GBR 4837R
                </p>
              </div>
              <div style={{ textAlign: "right" }}>
                <div
                  style={{
                    fontFamily: "'Source Code Pro', monospace",
                    fontSize: 28,
                    fontWeight: 500,
                    color: C.secondary,
                    lineHeight: 1,
                  }}
                >
                  1.031
                </div>
                <p
                  style={{
                    fontFamily: "'Source Sans 3', sans-serif",
                    fontSize: 11,
                    color: "rgba(255,255,255,0.45)",
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                    marginTop: 3,
                  }}
                >
                  IRC TCC 2024
                </p>
              </div>
            </div>

            {/* Card body */}
            <div style={{ padding: "24px 28px" }}>
              {/* Rating row */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: 16,
                  marginBottom: 20,
                }}
              >
                {[
                  { label: "Fleet Rank", value: "7 / 42", sub: "J/112E Class" },
                  { label: "Rating Trend", value: "▼ 0.003", sub: "Last 12 mo." },
                  { label: "Optimization", value: "82%", sub: "Potential" },
                  { label: "Races", value: "147", sub: "Since 2018" },
                ].map((item) => (
                  <div key={item.label} style={{ textAlign: "center" }}>
                    <div
                      style={{
                        fontFamily: "'Source Code Pro', monospace",
                        fontSize: 18,
                        fontWeight: 500,
                        color: item.value.includes("▼") ? "#2D8A4E" : C.primary,
                      }}
                    >
                      {item.value}
                    </div>
                    <div
                      style={{
                        fontFamily: "'Source Sans 3', sans-serif",
                        fontSize: 12,
                        fontWeight: 600,
                        color: C.text,
                        marginTop: 2,
                      }}
                    >
                      {item.label}
                    </div>
                    <div
                      style={{
                        fontFamily: "'Source Sans 3', sans-serif",
                        fontSize: 11,
                        color: C.muted,
                      }}
                    >
                      {item.sub}
                    </div>
                  </div>
                ))}
              </div>

              {/* Divider line */}
              <div style={{ height: 1, background: C.border, margin: "0 0 18px" }} />

              {/* Tags / certificates */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {["IRC 2024", "IRC 2023", "ORC 2024", "ORC 2023"].map((cert) => (
                    <span
                      key={cert}
                      style={{
                        fontFamily: "'Source Code Pro', monospace",
                        fontSize: 11,
                        fontWeight: 500,
                        color: C.muted,
                        background: `${C.border}66`,
                        padding: "3px 10px",
                        borderRadius: 4,
                      }}
                    >
                      {cert}
                    </span>
                  ))}
                </div>
                <Stars count={4} />
              </div>
            </div>
          </div>
        </section>

        <Divider />

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            SEARCH BAR (Standalone)
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <section style={{ padding: "56px 24px 0", maxWidth: 640, margin: "0 auto" }}>
          <div style={{ textAlign: "center", marginBottom: 32 }}>
            <p
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: "0.18em",
                textTransform: "uppercase",
                color: C.secondary,
                marginBottom: 12,
              }}
            >
              Find Your Boat
            </p>
            <h2
              style={{
                fontFamily: "'Cinzel Decorative', cursive",
                fontWeight: 700,
                fontSize: "clamp(1.3rem, 2.5vw, 1.8rem)",
                color: C.primary,
                lineHeight: 1.3,
              }}
            >
              Search by Name or Sail Number
            </h2>
          </div>

          {/* Search on cream background */}
          <div
            className="brand-a-search"
            style={{
              position: "relative",
              background: C.white,
              borderRadius: 48,
              boxShadow: "0 2px 16px rgba(10,34,64,0.08)",
              border: `1px solid ${C.border}`,
              display: "flex",
              alignItems: "center",
              transition: "box-shadow 0.3s ease",
            }}
          >
            <div style={{ paddingLeft: 22, display: "flex", alignItems: "center" }}>
              <SearchIcon color={C.muted} size={20} />
            </div>
            <input
              type="text"
              readOnly
              tabIndex={-1}
              placeholder="e.g. Alchemy, GBR 4837R, J/112E..."
              style={{
                flex: 1,
                height: 54,
                border: "none",
                outline: "none",
                background: "transparent",
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 15,
                color: C.text,
                paddingLeft: 14,
                paddingRight: 14,
              }}
            />
            <button
              style={{
                background: C.primary,
                color: C.white,
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 14,
                fontWeight: 700,
                letterSpacing: "0.04em",
                border: "none",
                borderRadius: 48,
                padding: "10px 28px",
                marginRight: 6,
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              Search
            </button>
          </div>

          {/* Recent searches hint */}
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              gap: 8,
              marginTop: 16,
              flexWrap: "wrap",
            }}
          >
            {["Foggy Dew", "Ino XXX", "Hooligan X", "Baraka GP"].map((boat) => (
              <span
                key={boat}
                style={{
                  fontFamily: "'Source Sans 3', sans-serif",
                  fontSize: 12,
                  color: C.muted,
                  background: C.white,
                  border: `1px solid ${C.border}`,
                  padding: "4px 14px",
                  borderRadius: 20,
                  cursor: "pointer",
                }}
              >
                {boat}
              </span>
            ))}
          </div>
        </section>

        <Divider />

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            COLOR PALETTE
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <section style={{ padding: "56px 24px 0", maxWidth: 800, margin: "0 auto" }}>
          <div style={{ textAlign: "center", marginBottom: 40 }}>
            <p
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: "0.18em",
                textTransform: "uppercase",
                color: C.secondary,
                marginBottom: 12,
              }}
            >
              Brand System
            </p>
            <h2
              style={{
                fontFamily: "'Cinzel Decorative', cursive",
                fontWeight: 700,
                fontSize: "clamp(1.3rem, 2.5vw, 1.8rem)",
                color: C.primary,
                lineHeight: 1.3,
              }}
            >
              Color Palette
            </h2>
          </div>

          <div
            style={{
              display: "flex",
              justifyContent: "center",
              gap: "clamp(16px, 3vw, 36px)",
              flexWrap: "wrap",
            }}
          >
            <Swatch hex={C.primary}   name="Deep Oxford Blue" label="Primary" />
            <Swatch hex={C.secondary} name="Burnished Brass"  label="Secondary" />
            <Swatch hex={C.accent}    name="Crimson"          label="Accent" />
            <Swatch hex={C.bg}        name="Cream"            label="Background" />
            <Swatch hex={C.muted}     name="Slate"            label="Muted" />
            <Swatch hex={C.border}    name="Warm Gray"        label="Border" />
          </div>
        </section>

        <Divider />

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            TYPOGRAPHY SAMPLES
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <section style={{ padding: "56px 24px 0", maxWidth: 720, margin: "0 auto" }}>
          <div style={{ textAlign: "center", marginBottom: 40 }}>
            <p
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: "0.18em",
                textTransform: "uppercase",
                color: C.secondary,
                marginBottom: 12,
              }}
            >
              Brand System
            </p>
            <h2
              style={{
                fontFamily: "'Cinzel Decorative', cursive",
                fontWeight: 700,
                fontSize: "clamp(1.3rem, 2.5vw, 1.8rem)",
                color: C.primary,
                lineHeight: 1.3,
              }}
            >
              Typography
            </h2>
          </div>

          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 36,
            }}
          >
            {/* Display / Cinzel Decorative */}
            <div
              style={{
                background: C.white,
                borderRadius: 12,
                border: `1px solid ${C.border}`,
                padding: "28px 32px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 16 }}>
                <span
                  style={{
                    fontFamily: "'Source Sans 3', sans-serif",
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: "0.14em",
                    textTransform: "uppercase",
                    color: C.muted,
                  }}
                >
                  Display &mdash; Cinzel Decorative
                </span>
                <span
                  style={{
                    fontFamily: "'Source Code Pro', monospace",
                    fontSize: 11,
                    color: C.border,
                  }}
                >
                  700 Bold
                </span>
              </div>
              <p
                style={{
                  fontFamily: "'Cinzel Decorative', cursive",
                  fontWeight: 700,
                  fontSize: "clamp(1.8rem, 4vw, 3rem)",
                  letterSpacing: "0.02em",
                  lineHeight: 1.2,
                  color: C.primary,
                }}
              >
                The Timeless Pursuit
              </p>
              <p
                style={{
                  fontFamily: "'Cinzel Decorative', cursive",
                  fontWeight: 700,
                  fontSize: 18,
                  letterSpacing: "0.1em",
                  color: C.primary,
                  marginTop: 16,
                }}
              >
                SAIL RATINGS
              </p>
              <p
                style={{
                  fontFamily: "'Cinzel Decorative', cursive",
                  fontWeight: 400,
                  fontSize: 15,
                  letterSpacing: "0.06em",
                  color: C.muted,
                  marginTop: 8,
                }}
              >
                Aa Bb Cc Dd Ee Ff Gg Hh Ii Jj Kk Ll Mm Nn Oo Pp
              </p>
            </div>

            {/* Body / Source Sans 3 */}
            <div
              style={{
                background: C.white,
                borderRadius: 12,
                border: `1px solid ${C.border}`,
                padding: "28px 32px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 16 }}>
                <span
                  style={{
                    fontFamily: "'Source Sans 3', sans-serif",
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: "0.14em",
                    textTransform: "uppercase",
                    color: C.muted,
                  }}
                >
                  Body &mdash; Source Sans 3
                </span>
                <span
                  style={{
                    fontFamily: "'Source Code Pro', monospace",
                    fontSize: 11,
                    color: C.border,
                  }}
                >
                  300 / 400 / 600 / 700
                </span>
              </div>
              <p
                style={{
                  fontFamily: "'Source Sans 3', sans-serif",
                  fontWeight: 300,
                  fontSize: 22,
                  lineHeight: 1.4,
                  color: C.primary,
                  marginBottom: 10,
                }}
              >
                Light 300 &mdash; We analyze every certificate to find where your rating points are hiding.
              </p>
              <p
                style={{
                  fontFamily: "'Source Sans 3', sans-serif",
                  fontWeight: 400,
                  fontSize: 16,
                  lineHeight: 1.7,
                  color: C.text,
                  marginBottom: 10,
                }}
              >
                Regular 400 &mdash; Rating analysis draws on 31,000 race results and 14 years of historical certificate data. Every parameter is cross-referenced against fleet competitors to identify optimization opportunities.
              </p>
              <p
                style={{
                  fontFamily: "'Source Sans 3', sans-serif",
                  fontWeight: 600,
                  fontSize: 14,
                  lineHeight: 1.5,
                  color: C.text,
                  marginBottom: 10,
                }}
              >
                Semibold 600 &mdash; IRC TCC 1.031 &middot; Fleet Position 7/42 &middot; Optimization Potential 82%
              </p>
              <p
                style={{
                  fontFamily: "'Source Sans 3', sans-serif",
                  fontWeight: 700,
                  fontSize: 14,
                  lineHeight: 1.5,
                  color: C.text,
                }}
              >
                Bold 700 &mdash; CRITICAL: Your draft measurement appears 15mm above fleet median.
              </p>
            </div>

            {/* Mono / Source Code Pro */}
            <div
              style={{
                background: C.white,
                borderRadius: 12,
                border: `1px solid ${C.border}`,
                padding: "28px 32px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 16 }}>
                <span
                  style={{
                    fontFamily: "'Source Sans 3', sans-serif",
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: "0.14em",
                    textTransform: "uppercase",
                    color: C.muted,
                  }}
                >
                  Mono &mdash; Source Code Pro
                </span>
                <span
                  style={{
                    fontFamily: "'Source Code Pro', monospace",
                    fontSize: 11,
                    color: C.border,
                  }}
                >
                  400 / 500
                </span>
              </div>
              <p
                style={{
                  fontFamily: "'Source Code Pro', monospace",
                  fontSize: 32,
                  fontWeight: 500,
                  color: C.primary,
                  marginBottom: 8,
                }}
              >
                1.031
              </p>
              <p
                style={{
                  fontFamily: "'Source Code Pro', monospace",
                  fontSize: 14,
                  color: C.muted,
                  lineHeight: 1.8,
                }}
              >
                TCC: 1.031 &nbsp;|&nbsp; GPH: 612.4 &nbsp;|&nbsp; TMF: 0.9847<br />
                Dspl: 8,450 kg &nbsp;|&nbsp; Draft: 2.15m &nbsp;|&nbsp; SA: 86.2 m&sup2;
              </p>
            </div>
          </div>
        </section>

        <Divider />

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            CTA SECTION
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <section
          style={{
            padding: "80px 24px",
            maxWidth: 720,
            margin: "0 auto",
            textAlign: "center",
          }}
        >
          <CompassRose size={40} color={C.secondary} />

          <h2
            style={{
              fontFamily: "'Cinzel Decorative', cursive",
              fontWeight: 700,
              fontSize: "clamp(1.5rem, 3.5vw, 2.4rem)",
              color: C.primary,
              lineHeight: 1.2,
              marginTop: 24,
              maxWidth: "20ch",
              marginLeft: "auto",
              marginRight: "auto",
            }}
          >
            Ready to find your advantage?
          </h2>

          <p
            style={{
              fontFamily: "'Source Sans 3', sans-serif",
              fontSize: 17,
              fontWeight: 300,
              color: C.muted,
              lineHeight: 1.7,
              maxWidth: 460,
              margin: "18px auto 0",
            }}
          >
            Join thousands of competitive sailors who trust Sail Ratings to uncover
            the hidden details in their racing certificates.
          </p>

          <div style={{ display: "flex", justifyContent: "center", gap: 16, marginTop: 36, flexWrap: "wrap" }}>
            <button
              className="brand-a-cta-primary"
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 15,
                fontWeight: 700,
                letterSpacing: "0.04em",
                color: C.primary,
                background: "transparent",
                border: `2px solid ${C.secondary}`,
                borderRadius: 6,
                padding: "14px 36px",
                cursor: "pointer",
              }}
            >
              Analyze My Boat
            </button>
            <button
              className="brand-a-cta-secondary"
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 15,
                fontWeight: 600,
                letterSpacing: "0.04em",
                color: C.muted,
                background: "transparent",
                border: `1.5px solid ${C.border}`,
                borderRadius: 6,
                padding: "14px 36px",
                cursor: "pointer",
              }}
            >
              View Sample Report
            </button>
          </div>

          {/* Trust line */}
          <p
            style={{
              fontFamily: "'Source Sans 3', sans-serif",
              fontSize: 12,
              color: C.border,
              marginTop: 28,
              letterSpacing: "0.06em",
            }}
          >
            No account required &middot; Free basic analysis &middot; Full report from &pound;9.95
          </p>
        </section>

        {/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            FOOTER
           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
        <footer
          style={{
            borderTop: `1px solid ${C.border}`,
            padding: "32px 24px",
            marginTop: 0,
          }}
        >
          <div
            style={{
              maxWidth: 800,
              margin: "0 auto",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 16,
            }}
          >
            {/* Footer logo */}
            <div style={{ display: "flex", alignItems: "center", gap: 10, opacity: 0.4 }}>
              <CompassRose size={22} color={C.primary} />
              <span
                style={{
                  fontFamily: "'Cinzel Decorative', cursive",
                  fontWeight: 700,
                  fontSize: 13,
                  letterSpacing: "0.1em",
                  color: C.primary,
                }}
              >
                SAIL RATINGS
              </span>
            </div>
            <p
              style={{
                fontFamily: "'Source Sans 3', sans-serif",
                fontSize: 12,
                color: C.muted,
                textAlign: "center",
                lineHeight: 1.6,
                maxWidth: 480,
              }}
            >
              Rating data sourced from public certificates.
              Not affiliated with the RORC Rating Office or ORC.
            </p>
            <p
              style={{
                fontFamily: "'Source Code Pro', monospace",
                fontSize: 11,
                color: C.border,
              }}
            >
              &copy; 2026 Sail Ratings
            </p>
          </div>
        </footer>
      </div>
    </>
  );
}

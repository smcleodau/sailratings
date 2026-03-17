"use client";

import { useState } from "react";

/* ────────────────────────────────────────────
   Option B: "Modern Performance"
   Dark, technical, aggressive — neon green accent
   ──────────────────────────────────────────── */

// ── Tokens ──────────────────────────────────
const C = {
  primary:    "#0F1115",
  secondary:  "#3E4C59",
  accent:     "#00FF95",
  bg:         "#0F1115",
  text:       "#FFFFFF",
  muted:      "#9BA3AF",
  border:     "#2D333B",
  cardBg:     "#161A1F",
  inputBg:    "#1A1F25",
} as const;

// ── SVG logo: abstract hydrofoil/sail arrow ─
function Logo({ size = 38 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="SailRatings logo"
    >
      {/* Hydrofoil body — angular upward-right arrow form */}
      <path
        d="M8 40L22 8L28 20L44 12L30 28L20 24L8 40Z"
        fill={C.accent}
        opacity={0.95}
      />
      {/* Speed line / foil trail */}
      <path
        d="M6 42L18 26"
        stroke={C.accent}
        strokeWidth="2"
        strokeLinecap="round"
        opacity={0.4}
      />
      <path
        d="M12 44L22 30"
        stroke={C.accent}
        strokeWidth="1.5"
        strokeLinecap="round"
        opacity={0.25}
      />
    </svg>
  );
}

// ── Wordmark ────────────────────────────────
function Wordmark({ fontSize = 26 }: { fontSize?: number }) {
  return (
    <span
      style={{
        fontFamily: "'Montserrat', sans-serif",
        fontWeight: 900,
        fontStyle: "italic",
        fontSize,
        letterSpacing: "-0.02em",
        color: C.text,
        lineHeight: 1,
      }}
    >
      Sail<span style={{ color: C.accent }}>Ratings</span>
    </span>
  );
}

// ── Color swatch ────────────────────────────
function Swatch({ hex, name, label }: { hex: string; name: string; label: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        style={{
          width: 80,
          height: 80,
          borderRadius: 10,
          background: hex,
          border: `1px solid ${C.border}`,
          boxShadow: hex === C.accent ? `0 0 24px ${C.accent}40` : undefined,
        }}
      />
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: C.muted }}>{hex}</span>
      <span style={{ fontFamily: "'Inter', sans-serif", fontSize: 12, fontWeight: 600, color: C.text }}>{name}</span>
      <span style={{ fontFamily: "'Inter', sans-serif", fontSize: 11, color: C.muted }}>{label}</span>
    </div>
  );
}

// ── Stat pill (for boat card) ───────────────
function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "10px 16px",
        background: accent ? `${C.accent}12` : `${C.secondary}20`,
        borderRadius: 8,
        border: `1px solid ${accent ? C.accent + "30" : C.border}`,
        minWidth: 80,
      }}
    >
      <span
        style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 18,
          fontWeight: 700,
          color: accent ? C.accent : C.text,
          lineHeight: 1.2,
        }}
      >
        {value}
      </span>
      <span
        style={{
          fontFamily: "'Inter', sans-serif",
          fontSize: 10,
          fontWeight: 500,
          color: C.muted,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          marginTop: 4,
        }}
      >
        {label}
      </span>
    </div>
  );
}

// ── Main page ───────────────────────────────
export default function BrandB() {
  const [searchQuery, setSearchQuery] = useState("");

  return (
    <>
      {/* Google Fonts */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&family=Montserrat:ital,wght@0,400;0,500;0,600;0,700;0,800;0,900;1,700;1,800;1,900&display=swap');

        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        html { scroll-behavior: smooth; }
        body { background: ${C.bg}; }

        ::selection {
          background: ${C.accent};
          color: ${C.primary};
        }

        @keyframes pulse-glow {
          0%, 100% { box-shadow: 0 0 20px ${C.accent}25, 0 0 60px ${C.accent}08; }
          50% { box-shadow: 0 0 30px ${C.accent}35, 0 0 80px ${C.accent}12; }
        }

        @keyframes fade-in-up {
          from { opacity: 0; transform: translateY(24px); }
          to { opacity: 1; transform: translateY(0); }
        }

        @keyframes scan-line {
          from { transform: translateX(-100%); }
          to { transform: translateX(100%); }
        }

        .fade-in-up {
          animation: fade-in-up 0.7s ease-out both;
        }
        .fade-in-up-d1 { animation-delay: 0.1s; }
        .fade-in-up-d2 { animation-delay: 0.2s; }
        .fade-in-up-d3 { animation-delay: 0.35s; }
        .fade-in-up-d4 { animation-delay: 0.5s; }
        .fade-in-up-d5 { animation-delay: 0.65s; }
      `}</style>

      <div
        style={{
          minHeight: "100vh",
          background: C.bg,
          color: C.text,
          fontFamily: "'Inter', sans-serif",
          overflowX: "hidden",
        }}
      >
        {/* ── Option Bar ───────────────────────── */}
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            zIndex: 100,
            background: `${C.primary}EE`,
            backdropFilter: "blur(16px)",
            WebkitBackdropFilter: "blur(16px)",
            borderBottom: `1px solid ${C.border}`,
          }}
        >
          <div
            style={{
              maxWidth: 1200,
              margin: "0 auto",
              padding: "12px 24px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span
                style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 11,
                  fontWeight: 600,
                  color: C.primary,
                  background: C.accent,
                  padding: "3px 10px",
                  borderRadius: 4,
                  letterSpacing: "0.05em",
                  textTransform: "uppercase",
                }}
              >
                Option B
              </span>
              <span
                style={{
                  fontFamily: "'Montserrat', sans-serif",
                  fontWeight: 700,
                  fontStyle: "italic",
                  fontSize: 14,
                  color: C.text,
                }}
              >
                Modern Performance
              </span>
            </div>
            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
              {["A", "B", "C", "D", "E"].map((opt) => (
                <a
                  key={opt}
                  href={`/brand/${opt.toLowerCase()}`}
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 12,
                    fontWeight: opt === "B" ? 700 : 500,
                    color: opt === "B" ? C.primary : C.muted,
                    background: opt === "B" ? C.accent : "transparent",
                    border: `1px solid ${opt === "B" ? C.accent : C.border}`,
                    borderRadius: 6,
                    padding: "5px 12px",
                    textDecoration: "none",
                    transition: "all 0.2s",
                  }}
                >
                  {opt}
                </a>
              ))}
            </div>
          </div>
        </div>

        {/* ── Hero ─────────────────────────────── */}
        <section
          style={{
            position: "relative",
            minHeight: "100vh",
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            alignItems: "center",
            textAlign: "center",
            padding: "120px 24px 80px",
            overflow: "hidden",
          }}
        >
          {/* Background image */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              backgroundImage: "url(/hero.jpg)",
              backgroundSize: "cover",
              backgroundPosition: "center 30%",
              filter: "brightness(0.25) saturate(0.3)",
            }}
          />
          {/* Dark gradient overlay */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: `linear-gradient(180deg, ${C.bg}CC 0%, ${C.bg}99 40%, ${C.bg}DD 80%, ${C.bg} 100%)`,
            }}
          />
          {/* Scan-line effect */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              overflow: "hidden",
              pointerEvents: "none",
              opacity: 0.03,
            }}
          >
            {Array.from({ length: 60 }).map((_, i) => (
              <div
                key={i}
                style={{
                  position: "absolute",
                  top: `${i * 1.67}%`,
                  left: 0,
                  right: 0,
                  height: 1,
                  background: C.accent,
                }}
              />
            ))}
          </div>
          {/* Accent glow top */}
          <div
            style={{
              position: "absolute",
              top: -200,
              left: "50%",
              transform: "translateX(-50%)",
              width: 600,
              height: 400,
              borderRadius: "50%",
              background: `radial-gradient(ellipse, ${C.accent}10 0%, transparent 70%)`,
              pointerEvents: "none",
            }}
          />

          {/* Hero content */}
          <div style={{ position: "relative", zIndex: 2, maxWidth: 900 }}>
            <div className="fade-in-up" style={{ marginBottom: 24 }}>
              <Logo size={56} />
            </div>

            <div className="fade-in-up fade-in-up-d1" style={{ marginBottom: 16 }}>
              <Wordmark fontSize={32} />
            </div>

            <p
              className="fade-in-up fade-in-up-d1"
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 11,
                fontWeight: 500,
                color: C.accent,
                textTransform: "uppercase",
                letterSpacing: "0.2em",
                marginBottom: 32,
              }}
            >
              Yacht Racing Rating Analysis
            </p>

            <h1
              className="fade-in-up fade-in-up-d2"
              style={{
                fontFamily: "'Montserrat', sans-serif",
                fontWeight: 900,
                fontStyle: "italic",
                fontSize: "clamp(3rem, 6vw, 5.5rem)",
                lineHeight: 1.02,
                letterSpacing: "-0.02em",
                color: C.text,
                marginBottom: 28,
              }}
            >
              Optimize your rating.
              <br />
              <span style={{ color: C.accent }}>Maximize your speed.</span>
            </h1>

            <p
              className="fade-in-up fade-in-up-d3"
              style={{
                fontFamily: "'Inter', sans-serif",
                fontSize: 18,
                fontWeight: 400,
                lineHeight: 1.65,
                color: C.muted,
                maxWidth: 560,
                margin: "0 auto 48px",
              }}
            >
              We analyze 31,000+ race results and every certificate ever published
              to find where your IRC and ORC rating points are hiding.
            </p>

            {/* Search bar */}
            <div
              className="fade-in-up fade-in-up-d4"
              style={{
                display: "flex",
                alignItems: "center",
                maxWidth: 560,
                margin: "0 auto",
                background: C.inputBg,
                border: `1px solid ${C.border}`,
                borderRadius: 12,
                padding: "4px 4px 4px 20px",
                transition: "border-color 0.3s, box-shadow 0.3s",
                animation: "pulse-glow 4s ease-in-out infinite",
              }}
            >
              {/* Search icon */}
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke={C.muted}
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ flexShrink: 0, marginRight: 12 }}
              >
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                type="text"
                placeholder="Search by boat name, sail number, or class..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  flex: 1,
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  fontFamily: "'Inter', sans-serif",
                  fontSize: 15,
                  color: C.text,
                  padding: "14px 0",
                }}
              />
              <button
                style={{
                  fontFamily: "'Montserrat', sans-serif",
                  fontWeight: 700,
                  fontSize: 14,
                  color: C.primary,
                  background: C.accent,
                  border: "none",
                  borderRadius: 9,
                  padding: "12px 28px",
                  cursor: "pointer",
                  letterSpacing: "-0.01em",
                  transition: "transform 0.15s, box-shadow 0.25s",
                  whiteSpace: "nowrap",
                }}
              >
                Analyze
              </button>
            </div>

            <p
              className="fade-in-up fade-in-up-d5"
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 11,
                color: C.muted,
                marginTop: 20,
                opacity: 0.6,
              }}
            >
              e.g. &quot;Bella Mente&quot; &middot; GBR 7096R &middot; TP52
            </p>
          </div>

          {/* Scroll indicator */}
          <div
            className="fade-in-up fade-in-up-d5"
            style={{
              position: "absolute",
              bottom: 40,
              left: "50%",
              transform: "translateX(-50%)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 10,
                color: C.muted,
                textTransform: "uppercase",
                letterSpacing: "0.15em",
              }}
            >
              Explore brand
            </span>
            <svg width="16" height="24" viewBox="0 0 16 24" fill="none">
              <rect x="1" y="1" width="14" height="22" rx="7" stroke={C.border} strokeWidth="1.5" />
              <circle cx="8" cy="8" r="2" fill={C.accent}>
                <animate attributeName="cy" values="8;16;8" dur="2s" repeatCount="indefinite" />
              </circle>
            </svg>
          </div>
        </section>

        {/* ── Color Palette Section ────────────── */}
        <section style={{ padding: "100px 24px", borderTop: `1px solid ${C.border}` }}>
          <div style={{ maxWidth: 1000, margin: "0 auto" }}>
            <SectionLabel text="Color System" />
            <h2
              style={{
                fontFamily: "'Montserrat', sans-serif",
                fontWeight: 800,
                fontStyle: "italic",
                fontSize: "clamp(2rem, 4vw, 3rem)",
                letterSpacing: "-0.02em",
                color: C.text,
                marginBottom: 12,
              }}
            >
              Dark-first palette
            </h2>
            <p
              style={{
                fontFamily: "'Inter', sans-serif",
                fontSize: 16,
                color: C.muted,
                lineHeight: 1.6,
                maxWidth: 520,
                marginBottom: 56,
              }}
            >
              Carbon black base with neon green accent delivers high contrast,
              technical authority, and instant visual hierarchy.
            </p>

            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 32,
              }}
            >
              <Swatch hex={C.primary}   name="Carbon Black"     label="Primary / Background" />
              <Swatch hex={C.secondary} name="Technical Grey"   label="Secondary" />
              <Swatch hex={C.accent}    name="Neon Green"       label="Accent / CTA" />
              <Swatch hex={C.text}      name="White"            label="Text" />
              <Swatch hex={C.muted}     name="Muted"            label="Captions" />
              <Swatch hex={C.border}    name="Border"           label="Dividers" />
              <Swatch hex={C.cardBg}    name="Card Surface"     label="Elevated bg" />
            </div>
          </div>
        </section>

        {/* ── Typography Section ───────────────── */}
        <section style={{ padding: "100px 24px", borderTop: `1px solid ${C.border}` }}>
          <div style={{ maxWidth: 1000, margin: "0 auto" }}>
            <SectionLabel text="Typography" />
            <h2
              style={{
                fontFamily: "'Montserrat', sans-serif",
                fontWeight: 800,
                fontStyle: "italic",
                fontSize: "clamp(2rem, 4vw, 3rem)",
                letterSpacing: "-0.02em",
                color: C.text,
                marginBottom: 56,
              }}
            >
              Three-font stack
            </h2>

            {/* Montserrat */}
            <div style={{ marginBottom: 56 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 20 }}>
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11,
                    color: C.accent,
                    textTransform: "uppercase",
                    letterSpacing: "0.12em",
                    fontWeight: 600,
                  }}
                >
                  Display
                </span>
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11,
                    color: C.muted,
                  }}
                >
                  Montserrat 800-900 Italic
                </span>
              </div>
              <p
                style={{
                  fontFamily: "'Montserrat', sans-serif",
                  fontWeight: 900,
                  fontStyle: "italic",
                  fontSize: "clamp(2.2rem, 5vw, 4rem)",
                  letterSpacing: "-0.02em",
                  lineHeight: 1.05,
                  color: C.text,
                  marginBottom: 12,
                }}
              >
                Rating optimization
                <br />
                <span style={{ color: C.accent }}>starts here.</span>
              </p>
              <p
                style={{
                  fontFamily: "'Montserrat', sans-serif",
                  fontWeight: 800,
                  fontStyle: "italic",
                  fontSize: 24,
                  letterSpacing: "-0.015em",
                  lineHeight: 1.2,
                  color: C.muted,
                }}
              >
                Subsection heading at weight 800
              </p>
            </div>

            {/* Inter */}
            <div style={{ marginBottom: 56 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 20 }}>
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11,
                    color: C.accent,
                    textTransform: "uppercase",
                    letterSpacing: "0.12em",
                    fontWeight: 600,
                  }}
                >
                  Body
                </span>
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11,
                    color: C.muted,
                  }}
                >
                  Inter 300-700
                </span>
              </div>
              <p
                style={{
                  fontFamily: "'Inter', sans-serif",
                  fontWeight: 400,
                  fontSize: 16,
                  lineHeight: 1.7,
                  color: C.text,
                  maxWidth: 640,
                  marginBottom: 12,
                }}
              >
                Body text is set in Inter at 16px with generous line-height for readability
                on dark backgrounds. The neutral geometry of Inter provides excellent
                legibility at small sizes while maintaining a modern, technical feel.
              </p>
              <p
                style={{
                  fontFamily: "'Inter', sans-serif",
                  fontWeight: 600,
                  fontSize: 14,
                  lineHeight: 1.6,
                  color: C.muted,
                }}
              >
                Semibold captions and labels at 14px &middot; Weight 600 &middot; Muted color for hierarchy
              </p>
            </div>

            {/* JetBrains Mono */}
            <div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 20 }}>
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11,
                    color: C.accent,
                    textTransform: "uppercase",
                    letterSpacing: "0.12em",
                    fontWeight: 600,
                  }}
                >
                  Mono
                </span>
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11,
                    color: C.muted,
                  }}
                >
                  JetBrains Mono 400-700
                </span>
              </div>
              <div
                style={{
                  background: C.cardBg,
                  border: `1px solid ${C.border}`,
                  borderRadius: 10,
                  padding: "20px 24px",
                  maxWidth: 480,
                }}
              >
                <p
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 14,
                    lineHeight: 1.8,
                    color: C.text,
                  }}
                >
                  <span style={{ color: C.accent }}>TCC:</span> 1.0842{" "}
                  <span style={{ color: C.muted }}>// IRC rating</span>
                  <br />
                  <span style={{ color: C.accent }}>GPH:</span> 523.7s/nm{" "}
                  <span style={{ color: C.muted }}>// ORC time</span>
                  <br />
                  <span style={{ color: C.accent }}>CDL:</span> 558.12{" "}
                  <span style={{ color: C.muted }}>// performance</span>
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ── Mock Boat Card Section ───────────── */}
        <section style={{ padding: "100px 24px", borderTop: `1px solid ${C.border}` }}>
          <div style={{ maxWidth: 1000, margin: "0 auto" }}>
            <SectionLabel text="Component: Boat Card" />
            <h2
              style={{
                fontFamily: "'Montserrat', sans-serif",
                fontWeight: 800,
                fontStyle: "italic",
                fontSize: "clamp(2rem, 4vw, 3rem)",
                letterSpacing: "-0.02em",
                color: C.text,
                marginBottom: 12,
              }}
            >
              Data-dense, scannable
            </h2>
            <p
              style={{
                fontFamily: "'Inter', sans-serif",
                fontSize: 16,
                color: C.muted,
                lineHeight: 1.6,
                maxWidth: 520,
                marginBottom: 56,
              }}
            >
              Every card surfaces the key metrics at a glance. Monospace for numbers,
              accent color draws the eye to actionable data.
            </p>

            {/* Boat Card */}
            <div
              style={{
                maxWidth: 580,
                background: C.cardBg,
                border: `1px solid ${C.border}`,
                borderRadius: 16,
                overflow: "hidden",
              }}
            >
              {/* Card header */}
              <div
                style={{
                  padding: "24px 28px 20px",
                  borderBottom: `1px solid ${C.border}`,
                  display: "flex",
                  alignItems: "flex-start",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                    <h3
                      style={{
                        fontFamily: "'Montserrat', sans-serif",
                        fontWeight: 800,
                        fontStyle: "italic",
                        fontSize: 22,
                        color: C.text,
                        letterSpacing: "-0.01em",
                      }}
                    >
                      Bella Mente
                    </h3>
                    <span
                      style={{
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: 10,
                        fontWeight: 600,
                        color: C.accent,
                        background: `${C.accent}15`,
                        padding: "2px 8px",
                        borderRadius: 4,
                        letterSpacing: "0.04em",
                      }}
                    >
                      IRC + ORC
                    </span>
                  </div>
                  <p
                    style={{
                      fontFamily: "'Inter', sans-serif",
                      fontSize: 13,
                      color: C.muted,
                    }}
                  >
                    Maxi 72 &middot; USA 45 &middot; Hap Fauth
                  </p>
                </div>
                <div
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11,
                    color: C.muted,
                    textAlign: "right",
                    lineHeight: 1.5,
                  }}
                >
                  <div>LOA <span style={{ color: C.text, fontWeight: 600 }}>21.82m</span></div>
                  <div>Beam <span style={{ color: C.text, fontWeight: 600 }}>5.50m</span></div>
                </div>
              </div>

              {/* Stats row */}
              <div
                style={{
                  padding: "20px 28px",
                  display: "flex",
                  gap: 12,
                  flexWrap: "wrap",
                }}
              >
                <Stat label="TCC" value="1.752" accent />
                <Stat label="GPH" value="389.2" />
                <Stat label="Races" value="47" />
                <Stat label="Win %" value="68%" accent />
              </div>

              {/* Performance bar */}
              <div style={{ padding: "0 28px 12px" }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: 8,
                  }}
                >
                  <span
                    style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 10,
                      color: C.muted,
                      textTransform: "uppercase",
                      letterSpacing: "0.08em",
                    }}
                  >
                    Performance Index
                  </span>
                  <span
                    style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 12,
                      fontWeight: 700,
                      color: C.accent,
                    }}
                  >
                    94.2
                  </span>
                </div>
                <div
                  style={{
                    height: 4,
                    background: C.border,
                    borderRadius: 2,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: "94.2%",
                      height: "100%",
                      background: `linear-gradient(90deg, ${C.accent}80, ${C.accent})`,
                      borderRadius: 2,
                    }}
                  />
                </div>
              </div>

              {/* Card footer */}
              <div
                style={{
                  padding: "16px 28px",
                  borderTop: `1px solid ${C.border}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <span
                  style={{
                    fontFamily: "'Inter', sans-serif",
                    fontSize: 12,
                    color: C.muted,
                  }}
                >
                  Certificate updated 14 Mar 2026
                </span>
                <button
                  style={{
                    fontFamily: "'Montserrat', sans-serif",
                    fontWeight: 700,
                    fontSize: 12,
                    color: C.primary,
                    background: C.accent,
                    border: "none",
                    borderRadius: 8,
                    padding: "8px 20px",
                    cursor: "pointer",
                    letterSpacing: "-0.01em",
                  }}
                >
                  Full Analysis &rarr;
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* ── Search Bar Section ───────────────── */}
        <section style={{ padding: "100px 24px", borderTop: `1px solid ${C.border}` }}>
          <div style={{ maxWidth: 1000, margin: "0 auto" }}>
            <SectionLabel text="Component: Search" />
            <h2
              style={{
                fontFamily: "'Montserrat', sans-serif",
                fontWeight: 800,
                fontStyle: "italic",
                fontSize: "clamp(2rem, 4vw, 3rem)",
                letterSpacing: "-0.02em",
                color: C.text,
                marginBottom: 12,
              }}
            >
              Find any boat, instantly
            </h2>
            <p
              style={{
                fontFamily: "'Inter', sans-serif",
                fontSize: 16,
                color: C.muted,
                lineHeight: 1.6,
                maxWidth: 520,
                marginBottom: 56,
              }}
            >
              Prominent search with glowing accent border on focus. Supports boat name,
              sail number, and class queries.
            </p>

            {/* Isolated search demo */}
            <div
              style={{
                maxWidth: 600,
                background: C.cardBg,
                border: `1px solid ${C.border}`,
                borderRadius: 16,
                padding: 32,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  background: C.inputBg,
                  border: `1px solid ${C.accent}60`,
                  borderRadius: 12,
                  padding: "4px 4px 4px 20px",
                  boxShadow: `0 0 20px ${C.accent}15, 0 0 60px ${C.accent}05`,
                }}
              >
                <svg
                  width="20"
                  height="20"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke={C.accent}
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{ flexShrink: 0, marginRight: 12 }}
                >
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <input
                  type="text"
                  placeholder="e.g. Rambler 88"
                  readOnly
                  style={{
                    flex: 1,
                    background: "transparent",
                    border: "none",
                    outline: "none",
                    fontFamily: "'Inter', sans-serif",
                    fontSize: 15,
                    color: C.text,
                    padding: "14px 0",
                  }}
                />
                <button
                  style={{
                    fontFamily: "'Montserrat', sans-serif",
                    fontWeight: 700,
                    fontSize: 14,
                    color: C.primary,
                    background: C.accent,
                    border: "none",
                    borderRadius: 9,
                    padding: "12px 28px",
                    cursor: "pointer",
                    whiteSpace: "nowrap",
                  }}
                >
                  Analyze
                </button>
              </div>

              {/* Mock autocomplete */}
              <div
                style={{
                  marginTop: 8,
                  background: C.inputBg,
                  border: `1px solid ${C.border}`,
                  borderRadius: 10,
                  overflow: "hidden",
                }}
              >
                {[
                  { name: "Rambler 88", sail: "USA 25588", cls: "Maxi" },
                  { name: "Rambler 100", sail: "USA 25100", cls: "Super Maxi" },
                  { name: "Rambutan", sail: "SIN 1042", cls: "J/112E" },
                ].map((item, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "12px 20px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      borderBottom: i < 2 ? `1px solid ${C.border}` : "none",
                      cursor: "pointer",
                      transition: "background 0.15s",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span
                        style={{
                          fontFamily: "'Inter', sans-serif",
                          fontWeight: 600,
                          fontSize: 14,
                          color: C.text,
                        }}
                      >
                        {item.name}
                      </span>
                      <span
                        style={{
                          fontFamily: "'JetBrains Mono', monospace",
                          fontSize: 11,
                          color: C.muted,
                        }}
                      >
                        {item.sail}
                      </span>
                    </div>
                    <span
                      style={{
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: 11,
                        color: C.accent,
                        opacity: 0.7,
                      }}
                    >
                      {item.cls}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ── CTA Section ──────────────────────── */}
        <section
          style={{
            position: "relative",
            padding: "120px 24px",
            borderTop: `1px solid ${C.border}`,
            overflow: "hidden",
          }}
        >
          {/* Accent gradient bg */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: `radial-gradient(ellipse 80% 60% at 50% 50%, ${C.accent}08 0%, transparent 70%)`,
              pointerEvents: "none",
            }}
          />
          {/* Grid pattern */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              opacity: 0.03,
              backgroundImage: `linear-gradient(${C.accent} 1px, transparent 1px), linear-gradient(90deg, ${C.accent} 1px, transparent 1px)`,
              backgroundSize: "60px 60px",
              pointerEvents: "none",
            }}
          />

          <div
            style={{
              position: "relative",
              zIndex: 2,
              maxWidth: 700,
              margin: "0 auto",
              textAlign: "center",
            }}
          >
            <p
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 11,
                fontWeight: 600,
                color: C.accent,
                textTransform: "uppercase",
                letterSpacing: "0.2em",
                marginBottom: 24,
              }}
            >
              Ready to gain an edge?
            </p>
            <h2
              style={{
                fontFamily: "'Montserrat', sans-serif",
                fontWeight: 900,
                fontStyle: "italic",
                fontSize: "clamp(2.2rem, 5vw, 4rem)",
                letterSpacing: "-0.02em",
                lineHeight: 1.05,
                color: C.text,
                marginBottom: 24,
              }}
            >
              Every tenth of a point
              <br />
              <span style={{ color: C.accent }}>is a boat-length.</span>
            </h2>
            <p
              style={{
                fontFamily: "'Inter', sans-serif",
                fontSize: 17,
                color: C.muted,
                lineHeight: 1.65,
                maxWidth: 480,
                margin: "0 auto 48px",
              }}
            >
              Get a detailed breakdown of your rating certificate, compare against
              your fleet, and find the optimizations that matter.
            </p>

            <div style={{ display: "flex", gap: 16, justifyContent: "center", flexWrap: "wrap" }}>
              <button
                style={{
                  fontFamily: "'Montserrat', sans-serif",
                  fontWeight: 700,
                  fontSize: 16,
                  color: C.primary,
                  background: C.accent,
                  border: "none",
                  borderRadius: 12,
                  padding: "16px 40px",
                  cursor: "pointer",
                  letterSpacing: "-0.01em",
                  boxShadow: `0 0 30px ${C.accent}30`,
                  transition: "transform 0.15s, box-shadow 0.25s",
                }}
              >
                Get Your Analysis
              </button>
              <button
                style={{
                  fontFamily: "'Montserrat', sans-serif",
                  fontWeight: 600,
                  fontSize: 16,
                  color: C.text,
                  background: "transparent",
                  border: `1px solid ${C.border}`,
                  borderRadius: 12,
                  padding: "16px 40px",
                  cursor: "pointer",
                  letterSpacing: "-0.01em",
                  transition: "border-color 0.2s",
                }}
              >
                View Sample Report
              </button>
            </div>

            {/* Trust signals */}
            <div
              style={{
                marginTop: 56,
                display: "flex",
                justifyContent: "center",
                gap: 40,
                flexWrap: "wrap",
              }}
            >
              {[
                { val: "31,000+", desc: "Races analyzed" },
                { val: "8,200+", desc: "Boats tracked" },
                { val: "12", desc: "Rating authorities" },
              ].map((s, i) => (
                <div key={i} style={{ textAlign: "center" }}>
                  <div
                    style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontWeight: 700,
                      fontSize: 28,
                      color: C.accent,
                      lineHeight: 1.2,
                    }}
                  >
                    {s.val}
                  </div>
                  <div
                    style={{
                      fontFamily: "'Inter', sans-serif",
                      fontSize: 12,
                      color: C.muted,
                      marginTop: 4,
                    }}
                  >
                    {s.desc}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── Footer ───────────────────────────── */}
        <footer
          style={{
            borderTop: `1px solid ${C.border}`,
            padding: "40px 24px",
          }}
        >
          <div
            style={{
              maxWidth: 1000,
              margin: "0 auto",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: 16,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Logo size={24} />
              <Wordmark fontSize={18} />
            </div>
            <p
              style={{
                fontFamily: "'Inter', sans-serif",
                fontSize: 12,
                color: C.muted,
              }}
            >
              Rating data sourced from public certificates. Not affiliated with the
              RORC Rating Office or ORC.
            </p>
          </div>
        </footer>
      </div>
    </>
  );
}

// ── Section label helper ──────────────────────
function SectionLabel({ text }: { text: string }) {
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        marginBottom: 20,
      }}
    >
      <div
        style={{
          width: 8,
          height: 8,
          borderRadius: 2,
          background: C.accent,
          boxShadow: `0 0 8px ${C.accent}50`,
        }}
      />
      <span
        style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11,
          fontWeight: 600,
          color: C.accent,
          textTransform: "uppercase",
          letterSpacing: "0.15em",
        }}
      >
        {text}
      </span>
    </div>
  );
}

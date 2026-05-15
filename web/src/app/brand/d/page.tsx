"use client";

import { useEffect, useState } from "react";

/* ─────────────────────────────────────────────
   Brand Option D — "Data Intelligence"
   Bloomberg-terminal aesthetic for yacht racing
   ───────────────────────────────────────────── */

const COLORS = {
  primary: "#0D1117",
  secondary: "#161B22",
  accent: "#33BFFF",
  bg: "#0D1117",
  text: "#E6EDF3",
  muted: "#8B949E",
  border: "#30363D",
};

const PALETTE = [
  { name: "Deep Space Black", hex: "#0D1117", role: "Primary / BG" },
  { name: "Darker Card", hex: "#161B22", role: "Secondary" },
  { name: "Electric Cyan", hex: "#33BFFF", role: "Accent" },
  { name: "Off-White", hex: "#E6EDF3", role: "Text" },
  { name: "Muted Gray", hex: "#8B949E", role: "Muted" },
  { name: "Border Dark", hex: "#30363D", role: "Border" },
];

/* Mini SVG: hull-line-graph logo concept */
function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      {/* hull curve */}
      <path
        d="M4 30 Q12 10, 20 18 Q28 26, 36 8"
        stroke="#33BFFF"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
      {/* data points */}
      <circle cx="4" cy="30" r="2.5" fill="#33BFFF" />
      <circle cx="14" cy="14" r="2.5" fill="#33BFFF" />
      <circle cx="20" cy="18" r="2.5" fill="#33BFFF" />
      <circle cx="28" cy="22" r="2.5" fill="#33BFFF" />
      <circle cx="36" cy="8" r="2.5" fill="#33BFFF" />
      {/* baseline */}
      <line
        x1="2"
        y1="36"
        x2="38"
        y2="36"
        stroke="#30363D"
        strokeWidth="1"
      />
    </svg>
  );
}

/* Mini spark-line for the boat card */
function SparkLine() {
  const points = "0,28 12,22 24,26 36,14 48,18 60,8 72,12 84,4";
  return (
    <svg
      width="84"
      height="32"
      viewBox="0 0 84 32"
      fill="none"
      style={{ display: "block" }}
    >
      <polyline
        points={points}
        stroke="#33BFFF"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
        fill="none"
      />
      <polyline
        points={`0,32 ${points} 84,32`}
        fill="url(#sparkGrad)"
        opacity="0.15"
      />
      <defs>
        <linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="32">
          <stop offset="0%" stopColor="#33BFFF" />
          <stop offset="100%" stopColor="#33BFFF" stopOpacity="0" />
        </linearGradient>
      </defs>
    </svg>
  );
}

/* ─── Animated counter hook ─── */
function useCounter(target: number, duration: number = 1400) {
  const [val, setVal] = useState(0);
  useEffect(() => {
    let start = 0;
    const step = target / (duration / 16);
    const id = setInterval(() => {
      start += step;
      if (start >= target) {
        setVal(target);
        clearInterval(id);
      } else {
        setVal(Math.round(start * 100) / 100);
      }
    }, 16);
    return () => clearInterval(id);
  }, [target, duration]);
  return val;
}

/* ─── Radar chart (mini) ─── */
function RadarChart() {
  const axes = 6;
  const cx = 60,
    cy = 60,
    r = 48;
  const values = [0.82, 0.65, 0.91, 0.74, 0.58, 0.88];

  const angleStep = (Math.PI * 2) / axes;
  const labels = ["SPD", "VMG", "RCH", "TWA", "LEN", "DSP"];

  const axisLines = Array.from({ length: axes }, (_, i) => {
    const a = angleStep * i - Math.PI / 2;
    return {
      x2: cx + r * Math.cos(a),
      y2: cy + r * Math.sin(a),
      lx: cx + (r + 14) * Math.cos(a),
      ly: cy + (r + 14) * Math.sin(a),
      label: labels[i],
    };
  });

  const dataPoints = values.map((v, i) => {
    const a = angleStep * i - Math.PI / 2;
    return `${cx + r * v * Math.cos(a)},${cy + r * v * Math.sin(a)}`;
  });

  const rings = [0.25, 0.5, 0.75, 1];

  return (
    <svg width="120" height="120" viewBox="0 0 120 120" fill="none">
      {/* rings */}
      {rings.map((s) => (
        <polygon
          key={s}
          points={Array.from({ length: axes }, (_, i) => {
            const a = angleStep * i - Math.PI / 2;
            return `${cx + r * s * Math.cos(a)},${cy + r * s * Math.sin(a)}`;
          }).join(" ")}
          stroke="#30363D"
          strokeWidth="0.5"
          fill="none"
        />
      ))}
      {/* axis lines */}
      {axisLines.map((ax, i) => (
        <g key={i}>
          <line
            x1={cx}
            y1={cy}
            x2={ax.x2}
            y2={ax.y2}
            stroke="#30363D"
            strokeWidth="0.5"
          />
          <text
            x={ax.lx}
            y={ax.ly}
            textAnchor="middle"
            dominantBaseline="central"
            fill="#8B949E"
            fontSize="7"
            fontFamily="'IBM Plex Mono', monospace"
          >
            {ax.label}
          </text>
        </g>
      ))}
      {/* data polygon */}
      <polygon
        points={dataPoints.join(" ")}
        fill="#33BFFF"
        fillOpacity="0.12"
        stroke="#33BFFF"
        strokeWidth="1.5"
      />
      {/* data dots */}
      {dataPoints.map((p, i) => {
        const [px, py] = p.split(",");
        return (
          <circle
            key={i}
            cx={px}
            cy={py}
            r="2.5"
            fill="#33BFFF"
          />
        );
      })}
    </svg>
  );
}

/* ─── Main Page Component ─── */
export default function BrandD() {
  const tdc = useCounter(1.018, 1600);
  const races = useCounter(31247, 2000);
  const certs = useCounter(4823, 1800);

  return (
    <>
      {/* Google Fonts */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap');
      `}</style>

      <div
        style={{
          fontFamily: "'IBM Plex Sans', sans-serif",
          background: COLORS.bg,
          color: COLORS.text,
          minHeight: "100vh",
          margin: 0,
          overflowX: "hidden",
        }}
      >
        {/* ══════════════ Option Banner ══════════════ */}
        <div
          style={{
            background: COLORS.secondary,
            borderBottom: `1px solid ${COLORS.border}`,
            padding: "10px 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: "12px",
            position: "sticky",
            top: 0,
            zIndex: 100,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span
              style={{
                background: COLORS.accent,
                color: COLORS.primary,
                padding: "2px 8px",
                fontWeight: 700,
                fontSize: "11px",
                letterSpacing: "0.05em",
              }}
            >
              OPTION D
            </span>
            <span style={{ color: COLORS.text, fontWeight: 600 }}>
              Data Intelligence
            </span>
            <span style={{ color: COLORS.muted }}>
              // Bloomberg Terminal Aesthetic
            </span>
          </div>
          <a
            href="/brand"
            style={{
              color: COLORS.accent,
              textDecoration: "none",
              fontSize: "12px",
              display: "flex",
              alignItems: "center",
              gap: "4px",
              transition: "opacity 0.2s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.7")}
            onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
          >
            <span style={{ fontSize: "14px" }}>&larr;</span> ALL OPTIONS
          </a>
        </div>

        {/* ══════════════ Navigation Bar ══════════════ */}
        <nav
          style={{
            background: `${COLORS.primary}E6`,
            backdropFilter: "blur(16px)",
            WebkitBackdropFilter: "blur(16px)",
            borderBottom: `1px solid ${COLORS.border}`,
            padding: "0 32px",
            height: "56px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            position: "sticky",
            top: "37px",
            zIndex: 90,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <LogoMark size={28} />
            <span
              style={{
                fontFamily: "'IBM Plex Mono', monospace",
                fontWeight: 700,
                fontSize: "16px",
                letterSpacing: "0.08em",
                color: COLORS.text,
              }}
            >
              SAIL<span style={{ color: COLORS.accent }}>//</span>RATINGS
            </span>
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "28px",
              fontFamily: "'IBM Plex Sans', sans-serif",
              fontSize: "13px",
              fontWeight: 500,
            }}
          >
            {["Analysis", "Database", "Compare", "Insights"].map((item) => (
              <span
                key={item}
                style={{
                  color: COLORS.muted,
                  cursor: "pointer",
                  transition: "color 0.2s",
                  letterSpacing: "0.02em",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.color = COLORS.accent)
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.color = COLORS.muted)
                }
              >
                {item}
              </span>
            ))}
            <span
              style={{
                color: COLORS.primary,
                background: COLORS.accent,
                padding: "6px 16px",
                fontSize: "12px",
                fontWeight: 600,
                fontFamily: "'IBM Plex Mono', monospace",
                letterSpacing: "0.04em",
                cursor: "pointer",
                transition: "opacity 0.2s",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.85")}
              onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
            >
              TERMINAL
            </span>
          </div>
        </nav>

        {/* ══════════════ HERO SECTION ══════════════ */}
        <section
          style={{
            position: "relative",
            minHeight: "85vh",
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
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
              backgroundPosition: "center 40%",
              filter: "brightness(0.18) saturate(0.3)",
            }}
          />
          {/* Dark overlay with grid pattern */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: `
                linear-gradient(180deg, ${COLORS.primary}CC 0%, ${COLORS.primary}99 40%, ${COLORS.primary}E6 100%),
                repeating-linear-gradient(0deg, transparent, transparent 59px, ${COLORS.border}33 60px),
                repeating-linear-gradient(90deg, transparent, transparent 59px, ${COLORS.border}33 60px)
              `,
            }}
          />
          {/* Scanline effect */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background:
                "repeating-linear-gradient(0deg, transparent 0px, transparent 2px, rgba(0,0,0,0.03) 2px, rgba(0,0,0,0.03) 4px)",
              pointerEvents: "none",
            }}
          />

          <div
            style={{
              position: "relative",
              zIndex: 2,
              maxWidth: "900px",
              margin: "0 auto",
              padding: "0 32px",
              textAlign: "center",
            }}
          >
            {/* Status line */}
            <div
              style={{
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: "12px",
                color: COLORS.accent,
                letterSpacing: "0.12em",
                marginBottom: "20px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: "8px",
              }}
            >
              <span
                style={{
                  width: "6px",
                  height: "6px",
                  borderRadius: "50%",
                  background: "#3FB950",
                  display: "inline-block",
                  boxShadow: "0 0 8px #3FB950",
                }}
              />
              LIVE DATA FEED &mdash; 31,247 RACES INDEXED
            </div>

            {/* Main headline */}
            <h1
              style={{
                fontFamily: "'IBM Plex Sans', sans-serif",
                fontWeight: 700,
                fontSize: "clamp(2rem, 4vw, 3.5rem)",
                lineHeight: 1.15,
                color: COLORS.text,
                margin: "0 0 20px",
                letterSpacing: "-0.01em",
              }}
            >
              Decode your rating.
              <br />
              <span style={{ color: COLORS.accent }}>
                Realize your potential.
              </span>
            </h1>

            {/* Subhead */}
            <p
              style={{
                fontFamily: "'IBM Plex Sans', sans-serif",
                fontSize: "clamp(1rem, 1.6vw, 1.2rem)",
                fontWeight: 400,
                color: COLORS.muted,
                maxWidth: "600px",
                margin: "0 auto 36px",
                lineHeight: 1.6,
              }}
            >
              Algorithmic analysis of every IRC and ORC certificate ever
              published. Find the hidden data points that define your
              competitive edge.
            </p>

            {/* ── Search Bar ── */}
            <div
              style={{
                background: COLORS.secondary,
                border: `1px solid ${COLORS.border}`,
                borderRadius: "2px",
                padding: "6px 6px 6px 20px",
                display: "flex",
                alignItems: "center",
                maxWidth: "560px",
                margin: "0 auto 32px",
                boxShadow: `0 0 0 1px ${COLORS.border}, 0 8px 32px rgba(0,0,0,0.4)`,
              }}
            >
              <span
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  color: COLORS.accent,
                  fontSize: "14px",
                  marginRight: "8px",
                  opacity: 0.6,
                }}
              >
                &gt;
              </span>
              <input
                type="text"
                placeholder="Search boat name, sail number, or class..."
                readOnly
                style={{
                  flex: 1,
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  color: COLORS.text,
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "14px",
                  padding: "10px 0",
                  letterSpacing: "0.01em",
                }}
              />
              <button
                style={{
                  background: COLORS.accent,
                  color: COLORS.primary,
                  border: "none",
                  padding: "10px 24px",
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontWeight: 700,
                  fontSize: "12px",
                  letterSpacing: "0.08em",
                  cursor: "pointer",
                  transition: "opacity 0.2s",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.85")}
                onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
              >
                QUERY
              </button>
            </div>

            {/* Stats row */}
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                gap: "48px",
                fontFamily: "'IBM Plex Mono', monospace",
              }}
            >
              {[
                {
                  label: "RACES ANALYZED",
                  value: Math.round(races).toLocaleString(),
                },
                {
                  label: "CERTIFICATES",
                  value: Math.round(certs).toLocaleString(),
                },
                { label: "AVG TCC", value: tdc.toFixed(3) },
              ].map((s) => (
                <div key={s.label} style={{ textAlign: "center" }}>
                  <div
                    style={{
                      fontSize: "24px",
                      fontWeight: 700,
                      color: COLORS.text,
                      lineHeight: 1,
                    }}
                  >
                    {s.value}
                  </div>
                  <div
                    style={{
                      fontSize: "10px",
                      color: COLORS.muted,
                      letterSpacing: "0.1em",
                      marginTop: "6px",
                    }}
                  >
                    {s.label}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Bottom fade */}
          <div
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              right: 0,
              height: "120px",
              background: `linear-gradient(transparent, ${COLORS.bg})`,
              zIndex: 1,
            }}
          />
        </section>

        {/* ══════════════ COLOR PALETTE ══════════════ */}
        <section
          style={{
            maxWidth: "1080px",
            margin: "0 auto",
            padding: "80px 32px 60px",
          }}
        >
          <div
            style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "11px",
              color: COLORS.accent,
              letterSpacing: "0.14em",
              marginBottom: "8px",
            }}
          >
            01 // COLOR SYSTEM
          </div>
          <h2
            style={{
              fontFamily: "'IBM Plex Sans', sans-serif",
              fontWeight: 700,
              fontSize: "28px",
              color: COLORS.text,
              margin: "0 0 36px",
            }}
          >
            Palette
          </h2>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
              gap: "12px",
            }}
          >
            {PALETTE.map((c) => (
              <div
                key={c.hex}
                style={{
                  background: COLORS.secondary,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: "2px",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    height: "72px",
                    background: c.hex,
                    borderBottom: `1px solid ${COLORS.border}`,
                  }}
                />
                <div style={{ padding: "12px 14px" }}>
                  <div
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: "13px",
                      fontWeight: 600,
                      color: COLORS.text,
                      marginBottom: "2px",
                    }}
                  >
                    {c.hex}
                  </div>
                  <div
                    style={{
                      fontSize: "11px",
                      color: COLORS.muted,
                      fontWeight: 500,
                    }}
                  >
                    {c.name}
                  </div>
                  <div
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: "10px",
                      color: COLORS.accent,
                      marginTop: "4px",
                      letterSpacing: "0.04em",
                    }}
                  >
                    {c.role}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ══════════════ TYPOGRAPHY ══════════════ */}
        <section
          style={{
            maxWidth: "1080px",
            margin: "0 auto",
            padding: "40px 32px 60px",
          }}
        >
          <div
            style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "11px",
              color: COLORS.accent,
              letterSpacing: "0.14em",
              marginBottom: "8px",
            }}
          >
            02 // TYPOGRAPHY
          </div>
          <h2
            style={{
              fontFamily: "'IBM Plex Sans', sans-serif",
              fontWeight: 700,
              fontSize: "28px",
              color: COLORS.text,
              margin: "0 0 36px",
            }}
          >
            Type System
          </h2>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "24px",
            }}
          >
            {/* Display */}
            <div
              style={{
                background: COLORS.secondary,
                border: `1px solid ${COLORS.border}`,
                borderRadius: "2px",
                padding: "28px",
              }}
            >
              <div
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "10px",
                  color: COLORS.accent,
                  letterSpacing: "0.12em",
                  marginBottom: "16px",
                }}
              >
                DISPLAY &mdash; IBM PLEX SANS 700
              </div>
              <div
                style={{
                  fontFamily: "'IBM Plex Sans', sans-serif",
                  fontWeight: 700,
                  fontSize: "36px",
                  color: COLORS.text,
                  lineHeight: 1.15,
                  marginBottom: "8px",
                }}
              >
                Rating Analysis
              </div>
              <div
                style={{
                  fontFamily: "'IBM Plex Sans', sans-serif",
                  fontWeight: 600,
                  fontSize: "22px",
                  color: COLORS.text,
                  lineHeight: 1.3,
                }}
              >
                Competitive Intelligence
              </div>
            </div>

            {/* Body */}
            <div
              style={{
                background: COLORS.secondary,
                border: `1px solid ${COLORS.border}`,
                borderRadius: "2px",
                padding: "28px",
              }}
            >
              <div
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "10px",
                  color: COLORS.accent,
                  letterSpacing: "0.12em",
                  marginBottom: "16px",
                }}
              >
                BODY &mdash; IBM PLEX SANS 400
              </div>
              <p
                style={{
                  fontFamily: "'IBM Plex Sans', sans-serif",
                  fontWeight: 400,
                  fontSize: "16px",
                  color: COLORS.text,
                  lineHeight: 1.65,
                  margin: 0,
                }}
              >
                Every certificate tells a story in numbers. We parse beam,
                displacement, sail area, and 47 other variables to surface the
                patterns that matter.
              </p>
              <p
                style={{
                  fontFamily: "'IBM Plex Sans', sans-serif",
                  fontWeight: 400,
                  fontSize: "14px",
                  color: COLORS.muted,
                  lineHeight: 1.65,
                  margin: "12px 0 0",
                }}
              >
                Secondary body text at 14px in muted gray for supporting
                information and metadata.
              </p>
            </div>

            {/* Mono / Data */}
            <div
              style={{
                background: COLORS.secondary,
                border: `1px solid ${COLORS.border}`,
                borderRadius: "2px",
                padding: "28px",
              }}
            >
              <div
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "10px",
                  color: COLORS.accent,
                  letterSpacing: "0.12em",
                  marginBottom: "16px",
                }}
              >
                DATA &mdash; IBM PLEX MONO 400-700
              </div>
              <div
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontWeight: 700,
                  fontSize: "28px",
                  color: COLORS.accent,
                  marginBottom: "8px",
                }}
              >
                TCC 1.018
              </div>
              <div
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontWeight: 400,
                  fontSize: "14px",
                  color: COLORS.text,
                  lineHeight: 1.8,
                }}
              >
                <span style={{ color: COLORS.muted }}>beam:</span> 3.52m
                <br />
                <span style={{ color: COLORS.muted }}>dspl:</span> 6,420kg
                <br />
                <span style={{ color: COLORS.muted }}>sa_fore:</span> 42.7m
                <sup>2</sup>
              </div>
            </div>

            {/* Wordmark */}
            <div
              style={{
                background: COLORS.secondary,
                border: `1px solid ${COLORS.border}`,
                borderRadius: "2px",
                padding: "28px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
              }}
            >
              <div
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "10px",
                  color: COLORS.accent,
                  letterSpacing: "0.12em",
                  marginBottom: "16px",
                }}
              >
                WORDMARK &mdash; IBM PLEX MONO 700
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "16px", marginBottom: "16px" }}>
                <LogoMark size={40} />
                <span
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontWeight: 700,
                    fontSize: "28px",
                    letterSpacing: "0.08em",
                    color: COLORS.text,
                  }}
                >
                  SAIL<span style={{ color: COLORS.accent }}>//</span>RATINGS
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                <LogoMark size={22} />
                <span
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontWeight: 700,
                    fontSize: "14px",
                    letterSpacing: "0.08em",
                    color: COLORS.text,
                  }}
                >
                  SAIL<span style={{ color: COLORS.accent }}>//</span>RATINGS
                </span>
              </div>
            </div>
          </div>
        </section>

        {/* ══════════════ BOAT CARD ══════════════ */}
        <section
          style={{
            maxWidth: "1080px",
            margin: "0 auto",
            padding: "40px 32px 60px",
          }}
        >
          <div
            style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "11px",
              color: COLORS.accent,
              letterSpacing: "0.14em",
              marginBottom: "8px",
            }}
          >
            03 // DATA COMPONENTS
          </div>
          <h2
            style={{
              fontFamily: "'IBM Plex Sans', sans-serif",
              fontWeight: 700,
              fontSize: "28px",
              color: COLORS.text,
              margin: "0 0 36px",
            }}
          >
            Boat Analysis Card
          </h2>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "24px",
            }}
          >
            {/* Main boat card */}
            <div
              style={{
                background: COLORS.secondary,
                border: `1px solid ${COLORS.border}`,
                borderRadius: "2px",
                overflow: "hidden",
              }}
            >
              {/* Card header */}
              <div
                style={{
                  padding: "16px 20px",
                  borderBottom: `1px solid ${COLORS.border}`,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <div
                    style={{
                      fontFamily: "'IBM Plex Sans', sans-serif",
                      fontWeight: 700,
                      fontSize: "18px",
                      color: COLORS.text,
                    }}
                  >
                    FOGGY DEW
                  </div>
                  <div
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: "11px",
                      color: COLORS.muted,
                      marginTop: "2px",
                    }}
                  >
                    J/109 &bull; IRL 3911 &bull; IRC
                  </div>
                </div>
                <div
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    textAlign: "right",
                  }}
                >
                  <div
                    style={{
                      fontSize: "24px",
                      fontWeight: 700,
                      color: COLORS.accent,
                      lineHeight: 1,
                    }}
                  >
                    1.018
                  </div>
                  <div
                    style={{
                      fontSize: "10px",
                      color: COLORS.muted,
                      letterSpacing: "0.06em",
                      marginTop: "2px",
                    }}
                  >
                    TCC RATING
                  </div>
                </div>
              </div>

              {/* Data grid */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr 1fr",
                  borderBottom: `1px solid ${COLORS.border}`,
                }}
              >
                {[
                  { label: "LOA", value: "10.34m" },
                  { label: "BEAM", value: "3.25m" },
                  { label: "DSPL", value: "4,536kg" },
                  { label: "DRAFT", value: "1.98m" },
                  { label: "SA MAIN", value: "31.2m\u00B2" },
                  { label: "SA FORE", value: "42.7m\u00B2" },
                ].map((d, i) => (
                  <div
                    key={d.label}
                    style={{
                      padding: "12px 16px",
                      borderRight:
                        (i + 1) % 3 !== 0
                          ? `1px solid ${COLORS.border}`
                          : "none",
                      borderBottom:
                        i < 3 ? `1px solid ${COLORS.border}` : "none",
                    }}
                  >
                    <div
                      style={{
                        fontFamily: "'IBM Plex Mono', monospace",
                        fontSize: "10px",
                        color: COLORS.muted,
                        letterSpacing: "0.08em",
                        marginBottom: "4px",
                      }}
                    >
                      {d.label}
                    </div>
                    <div
                      style={{
                        fontFamily: "'IBM Plex Mono', monospace",
                        fontSize: "15px",
                        fontWeight: 600,
                        color: COLORS.text,
                      }}
                    >
                      {d.value}
                    </div>
                  </div>
                ))}
              </div>

              {/* Rating trend */}
              <div
                style={{
                  padding: "16px 20px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: "10px",
                      color: COLORS.muted,
                      letterSpacing: "0.08em",
                      marginBottom: "6px",
                    }}
                  >
                    RATING TREND (24 MO)
                  </div>
                  <SparkLine />
                </div>
                <div style={{ textAlign: "right" }}>
                  <div
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: "13px",
                      fontWeight: 600,
                      color: "#3FB950",
                      display: "flex",
                      alignItems: "center",
                      gap: "4px",
                      justifyContent: "flex-end",
                    }}
                  >
                    <span style={{ fontSize: "10px" }}>&#9650;</span> +0.003
                  </div>
                  <div
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: "10px",
                      color: COLORS.muted,
                      marginTop: "2px",
                    }}
                  >
                    vs. prev cert
                  </div>
                </div>
              </div>
            </div>

            {/* Performance radar + ranking */}
            <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
              <div
                style={{
                  background: COLORS.secondary,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: "2px",
                  padding: "20px",
                  flex: 1,
                }}
              >
                <div
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: "10px",
                    color: COLORS.muted,
                    letterSpacing: "0.08em",
                    marginBottom: "12px",
                  }}
                >
                  PERFORMANCE PROFILE
                </div>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "24px",
                  }}
                >
                  <RadarChart />
                  <div
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: "12px",
                      lineHeight: 2,
                    }}
                  >
                    <div>
                      <span style={{ color: COLORS.muted }}>SPD</span>{" "}
                      <span style={{ color: COLORS.text, fontWeight: 600 }}>
                        82%
                      </span>
                    </div>
                    <div>
                      <span style={{ color: COLORS.muted }}>VMG</span>{" "}
                      <span style={{ color: COLORS.text, fontWeight: 600 }}>
                        65%
                      </span>
                    </div>
                    <div>
                      <span style={{ color: COLORS.muted }}>RCH</span>{" "}
                      <span style={{ color: COLORS.accent, fontWeight: 600 }}>
                        91%
                      </span>
                    </div>
                    <div>
                      <span style={{ color: COLORS.muted }}>TWA</span>{" "}
                      <span style={{ color: COLORS.text, fontWeight: 600 }}>
                        74%
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Fleet position */}
              <div
                style={{
                  background: COLORS.secondary,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: "2px",
                  padding: "20px",
                }}
              >
                <div
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: "10px",
                    color: COLORS.muted,
                    letterSpacing: "0.08em",
                    marginBottom: "12px",
                  }}
                >
                  FLEET POSITION &mdash; J/109 CLASS
                </div>
                {/* horizontal bar */}
                <div
                  style={{
                    background: COLORS.primary,
                    borderRadius: "2px",
                    height: "28px",
                    position: "relative",
                    overflow: "hidden",
                    border: `1px solid ${COLORS.border}`,
                  }}
                >
                  <div
                    style={{
                      width: "68%",
                      height: "100%",
                      background: `linear-gradient(90deg, ${COLORS.accent}33, ${COLORS.accent}88)`,
                      position: "relative",
                    }}
                  >
                    <span
                      style={{
                        position: "absolute",
                        right: "8px",
                        top: "50%",
                        transform: "translateY(-50%)",
                        fontFamily: "'IBM Plex Mono', monospace",
                        fontSize: "11px",
                        fontWeight: 700,
                        color: COLORS.text,
                      }}
                    >
                      P68
                    </span>
                  </div>
                </div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginTop: "8px",
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: "10px",
                    color: COLORS.muted,
                  }}
                >
                  <span>FASTEST</span>
                  <span>14 of 47 boats &bull; Top 30%</span>
                  <span>SLOWEST</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ══════════════ SEARCH BAR SECTION (standalone) ══════════════ */}
        <section
          style={{
            maxWidth: "1080px",
            margin: "0 auto",
            padding: "40px 32px 60px",
          }}
        >
          <div
            style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "11px",
              color: COLORS.accent,
              letterSpacing: "0.14em",
              marginBottom: "8px",
            }}
          >
            04 // SEARCH INTERFACE
          </div>
          <h2
            style={{
              fontFamily: "'IBM Plex Sans', sans-serif",
              fontWeight: 700,
              fontSize: "28px",
              color: COLORS.text,
              margin: "0 0 36px",
            }}
          >
            Terminal Search
          </h2>

          <div
            style={{
              background: COLORS.secondary,
              border: `1px solid ${COLORS.border}`,
              borderRadius: "2px",
              overflow: "hidden",
            }}
          >
            {/* Terminal top bar */}
            <div
              style={{
                background: COLORS.primary,
                borderBottom: `1px solid ${COLORS.border}`,
                padding: "8px 16px",
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <div
                style={{
                  width: "10px",
                  height: "10px",
                  borderRadius: "50%",
                  background: "#F85149",
                }}
              />
              <div
                style={{
                  width: "10px",
                  height: "10px",
                  borderRadius: "50%",
                  background: "#D29922",
                }}
              />
              <div
                style={{
                  width: "10px",
                  height: "10px",
                  borderRadius: "50%",
                  background: "#3FB950",
                }}
              />
              <span
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "11px",
                  color: COLORS.muted,
                  marginLeft: "8px",
                }}
              >
                sail://query
              </span>
            </div>

            <div style={{ padding: "20px 24px" }}>
              {/* Search input */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  marginBottom: "20px",
                }}
              >
                <span
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    color: COLORS.accent,
                    fontSize: "14px",
                  }}
                >
                  $
                </span>
                <input
                  type="text"
                  defaultValue="find --class J/109 --region ISORA --sort tcc"
                  readOnly
                  style={{
                    flex: 1,
                    background: COLORS.primary,
                    border: `1px solid ${COLORS.border}`,
                    borderRadius: "2px",
                    padding: "10px 14px",
                    color: COLORS.text,
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: "13px",
                    outline: "none",
                  }}
                />
                <button
                  style={{
                    background: COLORS.accent,
                    color: COLORS.primary,
                    border: "none",
                    padding: "10px 20px",
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontWeight: 700,
                    fontSize: "12px",
                    letterSpacing: "0.06em",
                    cursor: "pointer",
                  }}
                >
                  RUN
                </button>
              </div>

              {/* Mock results table */}
              <div
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "12px",
                }}
              >
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr",
                    padding: "8px 0",
                    borderBottom: `1px solid ${COLORS.border}`,
                    color: COLORS.muted,
                    fontSize: "10px",
                    letterSpacing: "0.08em",
                  }}
                >
                  <span>BOAT</span>
                  <span>SAIL</span>
                  <span>TCC</span>
                  <span>DELTA</span>
                  <span>RANK</span>
                </div>
                {[
                  {
                    name: "FOGGY DEW",
                    sail: "IRL 3911",
                    tcc: "1.018",
                    delta: "+0.003",
                    rank: "#14",
                    deltaColor: "#3FB950",
                  },
                  {
                    name: "STORM",
                    sail: "IRL 2204",
                    tcc: "1.015",
                    delta: "-0.001",
                    rank: "#18",
                    deltaColor: "#F85149",
                  },
                  {
                    name: "JOKER II",
                    sail: "IRL 1307",
                    tcc: "1.012",
                    delta: "+0.005",
                    rank: "#22",
                    deltaColor: "#3FB950",
                  },
                  {
                    name: "OUTRAJEOUS",
                    sail: "GBR 8440",
                    tcc: "1.009",
                    delta: "0.000",
                    rank: "#27",
                    deltaColor: COLORS.muted,
                  },
                ].map((row) => (
                  <div
                    key={row.name}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr",
                      padding: "10px 0",
                      borderBottom: `1px solid ${COLORS.border}22`,
                      color: COLORS.text,
                      alignItems: "center",
                      cursor: "pointer",
                      transition: "background 0.15s",
                    }}
                    onMouseEnter={(e) =>
                      (e.currentTarget.style.background = `${COLORS.primary}`)
                    }
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.background = "transparent")
                    }
                  >
                    <span style={{ fontWeight: 600 }}>{row.name}</span>
                    <span style={{ color: COLORS.muted }}>{row.sail}</span>
                    <span style={{ color: COLORS.accent, fontWeight: 600 }}>
                      {row.tcc}
                    </span>
                    <span style={{ color: row.deltaColor }}>{row.delta}</span>
                    <span style={{ color: COLORS.muted }}>{row.rank}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ══════════════ CTA SECTION ══════════════ */}
        <section
          style={{
            maxWidth: "1080px",
            margin: "0 auto",
            padding: "40px 32px 80px",
          }}
        >
          <div
            style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "11px",
              color: COLORS.accent,
              letterSpacing: "0.14em",
              marginBottom: "8px",
            }}
          >
            05 // CALL TO ACTION
          </div>
          <h2
            style={{
              fontFamily: "'IBM Plex Sans', sans-serif",
              fontWeight: 700,
              fontSize: "28px",
              color: COLORS.text,
              margin: "0 0 36px",
            }}
          >
            Conversion Block
          </h2>

          <div
            style={{
              position: "relative",
              background: COLORS.secondary,
              border: `1px solid ${COLORS.border}`,
              borderRadius: "2px",
              overflow: "hidden",
            }}
          >
            {/* Subtle grid pattern */}
            <div
              style={{
                position: "absolute",
                inset: 0,
                background: `
                  repeating-linear-gradient(0deg, transparent, transparent 39px, ${COLORS.border}22 40px),
                  repeating-linear-gradient(90deg, transparent, transparent 39px, ${COLORS.border}22 40px)
                `,
                pointerEvents: "none",
              }}
            />

            <div
              style={{
                position: "relative",
                padding: "64px 48px",
                textAlign: "center",
              }}
            >
              <div
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "12px",
                  color: COLORS.accent,
                  letterSpacing: "0.12em",
                  marginBottom: "16px",
                }}
              >
                ACCESS GRANTED
              </div>
              <h3
                style={{
                  fontFamily: "'IBM Plex Sans', sans-serif",
                  fontWeight: 700,
                  fontSize: "clamp(1.5rem, 3vw, 2.5rem)",
                  color: COLORS.text,
                  margin: "0 0 16px",
                  lineHeight: 1.2,
                }}
              >
                Your rating has a story.
                <br />
                <span style={{ color: COLORS.accent }}>
                  Read the data.
                </span>
              </h3>
              <p
                style={{
                  fontFamily: "'IBM Plex Sans', sans-serif",
                  fontSize: "16px",
                  color: COLORS.muted,
                  maxWidth: "500px",
                  margin: "0 auto 32px",
                  lineHeight: 1.6,
                }}
              >
                Unlock your full certificate analysis. Compare against the
                fleet. Discover where your rating points are hiding.
              </p>

              <div
                style={{
                  display: "flex",
                  gap: "12px",
                  justifyContent: "center",
                  flexWrap: "wrap",
                }}
              >
                <button
                  style={{
                    background: COLORS.accent,
                    color: COLORS.primary,
                    border: "none",
                    padding: "14px 36px",
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontWeight: 700,
                    fontSize: "13px",
                    letterSpacing: "0.08em",
                    cursor: "pointer",
                    transition: "all 0.2s",
                    boxShadow: `0 0 24px ${COLORS.accent}33`,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.boxShadow = `0 0 40px ${COLORS.accent}55`;
                    e.currentTarget.style.transform = "translateY(-1px)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.boxShadow = `0 0 24px ${COLORS.accent}33`;
                    e.currentTarget.style.transform = "translateY(0)";
                  }}
                >
                  GET FULL REPORT &mdash; $4.99
                </button>
                <button
                  style={{
                    background: "transparent",
                    color: COLORS.text,
                    border: `1px solid ${COLORS.border}`,
                    padding: "14px 36px",
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontWeight: 600,
                    fontSize: "13px",
                    letterSpacing: "0.06em",
                    cursor: "pointer",
                    transition: "border-color 0.2s",
                  }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.borderColor = COLORS.accent)
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.borderColor = COLORS.border)
                  }
                >
                  VIEW SAMPLE
                </button>
              </div>

              {/* Trust badges */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "center",
                  gap: "32px",
                  marginTop: "40px",
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: "11px",
                  color: COLORS.muted,
                }}
              >
                {[
                  { icon: "&#9679;", text: "31,247 races analyzed" },
                  { icon: "&#9679;", text: "4,823 active certificates" },
                  { icon: "&#9679;", text: "Updated daily" },
                ].map((badge) => (
                  <span
                    key={badge.text}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                    }}
                  >
                    <span
                      style={{ color: COLORS.accent, fontSize: "6px" }}
                      dangerouslySetInnerHTML={{ __html: badge.icon }}
                    />
                    {badge.text}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ══════════════ FOOTER ══════════════ */}
        <footer
          style={{
            borderTop: `1px solid ${COLORS.border}`,
            padding: "24px 32px",
          }}
        >
          <div
            style={{
              maxWidth: "1080px",
              margin: "0 auto",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "11px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <LogoMark size={18} />
              <span style={{ color: COLORS.muted }}>
                SAIL<span style={{ color: COLORS.accent }}>//</span>RATINGS
              </span>
            </div>
            <span style={{ color: `${COLORS.muted}88` }}>
              Brand Option D &mdash; Data Intelligence &mdash; Not affiliated
              with RORC or ORC
            </span>
          </div>
        </footer>
      </div>
    </>
  );
}

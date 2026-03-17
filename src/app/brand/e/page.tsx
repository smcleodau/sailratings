"use client";

import { useState } from "react";

/* ═══════════════════════════════════════════════════════════════════════════
   Brand Option E — "Boutique Consultancy"
   Sail Ratings: Yacht Racing Rating Analysis
   ═══════════════════════════════════════════════════════════════════════════ */

const COLORS = {
  primary:    "#4A4238",
  secondary:  "#8C8376",
  accent:     "#D4AF37",
  background: "#F9F6F0",
  text:       "#4A4238",
  muted:      "#9E9589",
  border:     "#EBE5D9",
};

/* ── Compass Rose Logo (SVG) ─────────────────────────────────────────────── */

function CompassLogo({ size = 48, color = COLORS.primary }: { size?: number; color?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="Sail Ratings compass logo"
    >
      {/* Outer thin circle — wax seal / hallmark feel */}
      <circle cx="50" cy="50" r="47" stroke={color} strokeWidth="1.2" />
      <circle cx="50" cy="50" r="44" stroke={color} strokeWidth="0.4" />

      {/* Cardinal points — elegant thin strokes */}
      {/* North */}
      <polygon points="50,12 53,40 50,36 47,40" fill={color} opacity="0.9" />
      {/* South */}
      <polygon points="50,88 53,60 50,64 47,60" fill={color} opacity="0.45" />
      {/* East */}
      <polygon points="88,50 60,47 64,50 60,53" fill={color} opacity="0.45" />
      {/* West */}
      <polygon points="12,50 40,47 36,50 40,53" fill={color} opacity="0.9" />

      {/* Intercardinal points — thinner, more delicate */}
      {/* NE */}
      <polygon points="79,21 57,43 58,40 55,41" fill={color} opacity="0.3" />
      {/* NW */}
      <polygon points="21,21 43,43 40,42 41,45" fill={color} opacity="0.3" />
      {/* SE */}
      <polygon points="79,79 57,57 60,58 59,55" fill={color} opacity="0.2" />
      {/* SW */}
      <polygon points="21,79 43,57 42,60 45,59" fill={color} opacity="0.2" />

      {/* Center circle */}
      <circle cx="50" cy="50" r="4" fill={color} opacity="0.8" />
      <circle cx="50" cy="50" r="2" fill={COLORS.background} />
    </svg>
  );
}

/* ── Color Swatch ────────────────────────────────────────────────────────── */

function Swatch({ hex, name, label }: { hex: string; name: string; label: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}>
      <div
        style={{
          width: "72px",
          height: "72px",
          backgroundColor: hex,
          border: `1px solid ${COLORS.border}`,
          borderRadius: "0",
        }}
      />
      <div style={{ textAlign: "center" }}>
        <div
          style={{
            fontFamily: "'Fira Code', monospace",
            fontSize: "11px",
            color: COLORS.text,
            letterSpacing: "0.02em",
          }}
        >
          {hex}
        </div>
        <div
          style={{
            fontFamily: "'Spectral', serif",
            fontSize: "12px",
            color: COLORS.muted,
            marginTop: "2px",
          }}
        >
          {name}
        </div>
        <div
          style={{
            fontFamily: "'Fira Code', monospace",
            fontSize: "10px",
            color: COLORS.muted,
            opacity: 0.7,
            marginTop: "1px",
          }}
        >
          {label}
        </div>
      </div>
    </div>
  );
}

/* ── Section Wrapper ─────────────────────────────────────────────────────── */

function Section({
  children,
  title,
  subtitle,
  bg = COLORS.background,
}: {
  children: React.ReactNode;
  title: string;
  subtitle?: string;
  bg?: string;
}) {
  return (
    <section
      style={{
        backgroundColor: bg,
        padding: "80px 24px",
      }}
    >
      <div style={{ maxWidth: "960px", margin: "0 auto" }}>
        <div style={{ marginBottom: "48px" }}>
          <div
            style={{
              fontFamily: "'Fira Code', monospace",
              fontSize: "10px",
              letterSpacing: "0.15em",
              textTransform: "uppercase",
              color: COLORS.accent,
              marginBottom: "12px",
            }}
          >
            {title}
          </div>
          {subtitle && (
            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontSize: "18px",
                color: COLORS.secondary,
                lineHeight: 1.6,
                maxWidth: "520px",
              }}
            >
              {subtitle}
            </p>
          )}
        </div>
        {children}
      </div>
    </section>
  );
}

/* ── Thin Divider ────────────────────────────────────────────────────────── */

function Divider() {
  return (
    <div
      style={{
        width: "40px",
        height: "1px",
        backgroundColor: COLORS.accent,
        opacity: 0.5,
        margin: "0 auto",
      }}
    />
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Main Page Component
   ═══════════════════════════════════════════════════════════════════════════ */

export default function BrandOptionE() {
  const [searchValue, setSearchValue] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);

  return (
    <>
      {/* Google Fonts */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Tenor+Sans&family=Spectral:ital,wght@0,400;0,500;0,600;1,400&family=Fira+Code:wght@400;500&display=swap');

        *, *::before, *::after {
          margin: 0;
          padding: 0;
          box-sizing: border-box;
        }

        html {
          -webkit-font-smoothing: antialiased;
          -moz-osx-font-smoothing: grayscale;
        }

        ::selection {
          background: ${COLORS.accent}33;
          color: ${COLORS.primary};
        }

        @keyframes fadeInUp {
          from {
            opacity: 0;
            transform: translateY(20px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }

        @keyframes subtleFloat {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-4px); }
        }

        .brand-e-hero-search::placeholder {
          color: ${COLORS.secondary};
          opacity: 0.6;
        }

        .brand-e-cta-input::placeholder {
          color: rgba(255,255,255,0.4);
        }
      `}</style>

      <div style={{ backgroundColor: COLORS.background, minHeight: "100vh" }}>

        {/* ── Option Bar ─────────────────────────────────────────────────── */}
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            zIndex: 100,
            backgroundColor: COLORS.primary,
            padding: "10px 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span
              style={{
                fontFamily: "'Fira Code', monospace",
                fontSize: "11px",
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                color: COLORS.accent,
              }}
            >
              Option E
            </span>
            <span
              style={{
                fontFamily: "'Spectral', serif",
                fontSize: "13px",
                color: "rgba(255,255,255,0.6)",
              }}
            >
              Boutique Consultancy
            </span>
          </div>
          <a
            href="/brand"
            style={{
              fontFamily: "'Spectral', serif",
              fontSize: "12px",
              color: "rgba(255,255,255,0.4)",
              textDecoration: "none",
              borderBottom: "1px solid rgba(255,255,255,0.15)",
              paddingBottom: "1px",
              transition: "color 0.3s ease",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "rgba(255,255,255,0.8)")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "rgba(255,255,255,0.4)")}
          >
            All Options
          </a>
        </div>

        {/* ── Header / Navigation ─────────────────────────────────────── */}
        <header
          style={{
            position: "fixed",
            top: "38px",
            left: 0,
            right: 0,
            zIndex: 90,
            backgroundColor: `${COLORS.background}F2`,
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            borderBottom: `1px solid ${COLORS.border}`,
            padding: "0 32px",
            height: "64px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          {/* Wordmark */}
          <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
            <CompassLogo size={34} />
            <span
              style={{
                fontFamily: "'Tenor Sans', sans-serif",
                fontSize: "18px",
                letterSpacing: "0.05em",
                color: COLORS.primary,
              }}
            >
              Sail Ratings
            </span>
          </div>

          {/* Nav */}
          <nav style={{ display: "flex", alignItems: "center", gap: "32px" }}>
            {["Analysis", "Ratings", "Fleet", "About"].map((item) => (
              <a
                key={item}
                href="#"
                onClick={(e) => e.preventDefault()}
                style={{
                  fontFamily: "'Spectral', serif",
                  fontSize: "14px",
                  color: COLORS.secondary,
                  textDecoration: "none",
                  letterSpacing: "0.01em",
                  transition: "color 0.3s ease",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.color = COLORS.primary)}
                onMouseLeave={(e) => (e.currentTarget.style.color = COLORS.secondary)}
              >
                {item}
              </a>
            ))}
            <a
              href="#"
              onClick={(e) => e.preventDefault()}
              style={{
                fontFamily: "'Spectral', serif",
                fontSize: "13px",
                fontWeight: 500,
                letterSpacing: "0.05em",
                color: COLORS.background,
                backgroundColor: COLORS.primary,
                padding: "8px 20px",
                textDecoration: "none",
                transition: "background-color 0.3s ease",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = COLORS.secondary)}
              onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = COLORS.primary)}
            >
              Sign In
            </a>
          </nav>
        </header>

        {/* ── Hero Section ────────────────────────────────────────────── */}
        <section
          style={{
            position: "relative",
            height: "85vh",
            minHeight: "600px",
            marginTop: "102px",
            overflow: "hidden",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {/* Background image with warm vintage overlay */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              backgroundImage: "url('/hero.jpg')",
              backgroundSize: "cover",
              backgroundPosition: "center 40%",
              filter: "saturate(0.35) contrast(0.9) brightness(0.85)",
            }}
          />
          {/* Warm overlay */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: `linear-gradient(
                180deg,
                ${COLORS.primary}CC 0%,
                ${COLORS.primary}99 30%,
                ${COLORS.primary}BB 70%,
                ${COLORS.primary}EE 100%
              )`,
            }}
          />
          {/* Subtle warm tint */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              backgroundColor: `${COLORS.accent}08`,
              mixBlendMode: "overlay",
            }}
          />

          {/* Content */}
          <div
            style={{
              position: "relative",
              zIndex: 10,
              textAlign: "center",
              padding: "0 24px",
              maxWidth: "800px",
              animation: "fadeInUp 1s ease-out",
            }}
          >
            {/* Small logo above headline */}
            <div
              style={{
                marginBottom: "32px",
                opacity: 0.7,
                animation: "fadeIn 1.2s ease-out",
              }}
            >
              <CompassLogo size={56} color={COLORS.background} />
            </div>

            <h1
              style={{
                fontFamily: "'Tenor Sans', sans-serif",
                fontSize: "clamp(2.25rem, 4.5vw, 3.75rem)",
                fontWeight: 400,
                letterSpacing: "0.03em",
                color: COLORS.background,
                lineHeight: 1.2,
                marginBottom: "24px",
              }}
            >
              Your partner in precision
              <br />
              rating strategy.
            </h1>

            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontSize: "18px",
                color: `${COLORS.background}99`,
                lineHeight: 1.65,
                maxWidth: "480px",
                margin: "0 auto 48px",
                fontStyle: "italic",
              }}
            >
              Bespoke analysis of IRC and ORC certificates, distilled into
              actionable insight for competitive sailors.
            </p>

            {/* Search bar */}
            <div
              style={{
                maxWidth: "520px",
                margin: "0 auto",
                position: "relative",
                animation: "fadeInUp 1.2s ease-out 0.2s both",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  backgroundColor: searchFocused ? `${COLORS.background}18` : `${COLORS.background}0D`,
                  border: `1px solid ${searchFocused ? COLORS.accent + "60" : COLORS.background + "20"}`,
                  transition: "all 0.4s ease",
                  overflow: "hidden",
                }}
              >
                {/* Search icon */}
                <div style={{ padding: "0 0 0 20px", display: "flex", alignItems: "center" }}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={`${COLORS.background}50`} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="11" cy="11" r="8" />
                    <line x1="21" y1="21" x2="16.65" y2="16.65" />
                  </svg>
                </div>
                <input
                  type="text"
                  value={searchValue}
                  onChange={(e) => setSearchValue(e.target.value)}
                  onFocus={() => setSearchFocused(true)}
                  onBlur={() => setSearchFocused(false)}
                  placeholder="Search by boat name, sail number, or class..."
                  className="brand-e-hero-search"
                  style={{
                    flex: 1,
                    background: "none",
                    border: "none",
                    outline: "none",
                    fontFamily: "'Spectral', serif",
                    fontSize: "15px",
                    color: COLORS.background,
                    padding: "16px 20px",
                    letterSpacing: "0.01em",
                  }}
                />
                <button
                  style={{
                    padding: "16px 24px",
                    fontFamily: "'Spectral', serif",
                    fontSize: "13px",
                    fontWeight: 500,
                    letterSpacing: "0.06em",
                    color: COLORS.primary,
                    backgroundColor: COLORS.accent,
                    border: "none",
                    cursor: "pointer",
                    transition: "background-color 0.3s ease",
                    whiteSpace: "nowrap",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "#C9A430")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = COLORS.accent)}
                >
                  Search
                </button>
              </div>
              <div
                style={{
                  marginTop: "12px",
                  fontFamily: "'Fira Code', monospace",
                  fontSize: "11px",
                  color: `${COLORS.background}35`,
                  letterSpacing: "0.02em",
                }}
              >
                e.g. WHISTLER &middot; GBR4582R &middot; J/112E
              </div>
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
              background: `linear-gradient(transparent, ${COLORS.background})`,
            }}
          />
        </section>

        {/* ── Color Palette ───────────────────────────────────────────── */}
        <Section
          title="Colour Palette"
          subtitle="A deliberately restrained palette grounded in natural warmth. Gold is used sparingly to signal authority and precision."
        >
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "32px",
              justifyContent: "flex-start",
            }}
          >
            <Swatch hex={COLORS.primary} name="Warm Charcoal" label="Primary" />
            <Swatch hex={COLORS.secondary} name="Warm Stone" label="Secondary" />
            <Swatch hex={COLORS.accent} name="Antique Gold" label="Accent" />
            <Swatch hex={COLORS.background} name="Warm Linen" label="Background" />
            <Swatch hex={COLORS.muted} name="Driftwood" label="Muted" />
            <Swatch hex={COLORS.border} name="Parchment" label="Border" />
          </div>

          {/* Accent usage note */}
          <div
            style={{
              marginTop: "40px",
              padding: "20px 24px",
              borderLeft: `2px solid ${COLORS.accent}`,
              backgroundColor: `${COLORS.accent}08`,
            }}
          >
            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontSize: "14px",
                fontStyle: "italic",
                color: COLORS.secondary,
                lineHeight: 1.65,
              }}
            >
              The Antique Gold accent is reserved for moments of emphasis: primary CTAs,
              active states, and the compass rose mark. Overuse dilutes its authority.
            </p>
          </div>
        </Section>

        <Divider />

        {/* ── Typography ──────────────────────────────────────────────── */}
        <Section
          title="Typography"
          subtitle="Three typefaces, each with a distinct role. The hierarchy is clear and uncluttered."
        >
          {/* Tenor Sans — Display */}
          <div style={{ marginBottom: "56px" }}>
            <div
              style={{
                fontFamily: "'Fira Code', monospace",
                fontSize: "10px",
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: COLORS.muted,
                marginBottom: "16px",
              }}
            >
              Display &mdash; Tenor Sans 400
            </div>
            <h2
              style={{
                fontFamily: "'Tenor Sans', sans-serif",
                fontWeight: 400,
                fontSize: "clamp(2.25rem, 4.5vw, 3.75rem)",
                letterSpacing: "0.05em",
                color: COLORS.primary,
                lineHeight: 1.15,
                marginBottom: "8px",
              }}
            >
              Sail Ratings
            </h2>
            <p
              style={{
                fontFamily: "'Tenor Sans', sans-serif",
                fontWeight: 400,
                fontSize: "24px",
                letterSpacing: "0.05em",
                color: COLORS.secondary,
                lineHeight: 1.3,
              }}
            >
              Your partner in precision rating strategy.
            </p>
          </div>

          {/* Spectral — Body */}
          <div style={{ marginBottom: "56px" }}>
            <div
              style={{
                fontFamily: "'Fira Code', monospace",
                fontSize: "10px",
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: COLORS.muted,
                marginBottom: "16px",
              }}
            >
              Body &mdash; Spectral 400 / 500 / 600
            </div>
            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontWeight: 400,
                fontSize: "18px",
                color: COLORS.text,
                lineHeight: 1.7,
                maxWidth: "600px",
                marginBottom: "16px",
              }}
            >
              Every certificate tells a story. We read between the lines, comparing
              displacement curves, sail areas, and stability indices to find the competitive
              advantage hidden in plain sight.
            </p>
            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontWeight: 500,
                fontSize: "18px",
                color: COLORS.text,
                lineHeight: 1.7,
                maxWidth: "600px",
                marginBottom: "8px",
              }}
            >
              Medium weight for emphasis within body copy.
            </p>
            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontWeight: 600,
                fontSize: "18px",
                color: COLORS.text,
                lineHeight: 1.7,
                maxWidth: "600px",
                marginBottom: "8px",
              }}
            >
              Semibold for strong emphasis and sub-headings.
            </p>
            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontWeight: 400,
                fontSize: "18px",
                fontStyle: "italic",
                color: COLORS.secondary,
                lineHeight: 1.7,
                maxWidth: "600px",
              }}
            >
              Italic for editorial asides and supporting narrative.
            </p>
          </div>

          {/* Fira Code — Mono */}
          <div>
            <div
              style={{
                fontFamily: "'Fira Code', monospace",
                fontSize: "10px",
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: COLORS.muted,
                marginBottom: "16px",
              }}
            >
              Mono &mdash; Fira Code 400
            </div>
            <div
              style={{
                fontFamily: "'Fira Code', monospace",
                fontSize: "14px",
                color: COLORS.text,
                lineHeight: 1.8,
                padding: "20px 24px",
                backgroundColor: `${COLORS.primary}08`,
                border: `1px solid ${COLORS.border}`,
              }}
            >
              <div>TCC 1.025 &middot; IRC 1.038 &middot; GPH 582.3</div>
              <div style={{ color: COLORS.muted }}>
                Sail No: GBR4582R &middot; Class: J/112E
              </div>
              <div style={{ color: COLORS.muted }}>
                CDL: 9.841m &middot; Displ: 7,650 kg
              </div>
            </div>
          </div>
        </Section>

        <Divider />

        {/* ── Mock Search Bar (standalone) ────────────────────────────── */}
        <Section
          title="Search Component"
          subtitle="The search is the primary interaction. It should feel effortless and precise."
        >
          <div
            style={{
              maxWidth: "640px",
              backgroundColor: "white",
              padding: "40px",
              border: `1px solid ${COLORS.border}`,
            }}
          >
            <div
              style={{
                fontFamily: "'Tenor Sans', sans-serif",
                fontSize: "20px",
                fontWeight: 400,
                letterSpacing: "0.05em",
                color: COLORS.primary,
                marginBottom: "8px",
              }}
            >
              Find a rating
            </div>
            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontSize: "14px",
                color: COLORS.muted,
                marginBottom: "24px",
                lineHeight: 1.5,
              }}
            >
              Enter a boat name, sail number, or class to begin your analysis.
            </p>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                border: `1px solid ${COLORS.border}`,
                transition: "border-color 0.3s ease",
              }}
            >
              <div style={{ padding: "0 0 0 16px", display: "flex", alignItems: "center" }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={COLORS.muted} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
              </div>
              <input
                type="text"
                placeholder="e.g. WHISTLER, GBR4582R, J/112E"
                className="brand-e-hero-search"
                style={{
                  flex: 1,
                  background: "none",
                  border: "none",
                  outline: "none",
                  fontFamily: "'Spectral', serif",
                  fontSize: "15px",
                  color: COLORS.text,
                  padding: "14px 16px",
                }}
              />
              <button
                style={{
                  fontFamily: "'Spectral', serif",
                  fontSize: "13px",
                  fontWeight: 500,
                  letterSpacing: "0.04em",
                  color: "white",
                  backgroundColor: COLORS.primary,
                  border: "none",
                  padding: "14px 24px",
                  cursor: "pointer",
                  transition: "background-color 0.3s ease",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = COLORS.secondary)}
                onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = COLORS.primary)}
              >
                Search
              </button>
            </div>
          </div>
        </Section>

        <Divider />

        {/* ── Mock Boat Card ──────────────────────────────────────────── */}
        <Section
          title="Boat Card"
          subtitle="The card presents key rating data at a glance, with progressive disclosure for deeper analysis."
        >
          <div
            style={{
              maxWidth: "640px",
              backgroundColor: "white",
              border: `1px solid ${COLORS.border}`,
              overflow: "hidden",
            }}
          >
            {/* Card header */}
            <div
              style={{
                padding: "28px 32px 24px",
                borderBottom: `1px solid ${COLORS.border}`,
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
              }}
            >
              <div>
                <div
                  style={{
                    fontFamily: "'Tenor Sans', sans-serif",
                    fontSize: "22px",
                    fontWeight: 400,
                    letterSpacing: "0.05em",
                    color: COLORS.primary,
                    marginBottom: "6px",
                  }}
                >
                  WHISTLER
                </div>
                <div
                  style={{
                    fontFamily: "'Spectral', serif",
                    fontSize: "14px",
                    color: COLORS.muted,
                  }}
                >
                  J/112E &middot; GBR4582R
                </div>
              </div>
              <div
                style={{
                  fontFamily: "'Fira Code', monospace",
                  fontSize: "11px",
                  letterSpacing: "0.06em",
                  color: COLORS.accent,
                  padding: "4px 10px",
                  border: `1px solid ${COLORS.accent}40`,
                  textTransform: "uppercase",
                }}
              >
                IRC &middot; ORC
              </div>
            </div>

            {/* Rating data grid */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 1fr",
                borderBottom: `1px solid ${COLORS.border}`,
              }}
            >
              {[
                { label: "TCC", value: "1.025", note: "IRC Rating" },
                { label: "GPH", value: "582.3", note: "Secs/Mile" },
                { label: "CDL", value: "9.841", note: "Metres" },
              ].map((item, i) => (
                <div
                  key={item.label}
                  style={{
                    padding: "24px 32px",
                    borderRight: i < 2 ? `1px solid ${COLORS.border}` : "none",
                  }}
                >
                  <div
                    style={{
                      fontFamily: "'Fira Code', monospace",
                      fontSize: "10px",
                      letterSpacing: "0.1em",
                      textTransform: "uppercase",
                      color: COLORS.muted,
                      marginBottom: "8px",
                    }}
                  >
                    {item.label}
                  </div>
                  <div
                    style={{
                      fontFamily: "'Tenor Sans', sans-serif",
                      fontSize: "28px",
                      fontWeight: 400,
                      color: COLORS.primary,
                      letterSpacing: "0.02em",
                      marginBottom: "4px",
                    }}
                  >
                    {item.value}
                  </div>
                  <div
                    style={{
                      fontFamily: "'Spectral', serif",
                      fontSize: "12px",
                      color: COLORS.muted,
                    }}
                  >
                    {item.note}
                  </div>
                </div>
              ))}
            </div>

            {/* Certificate details */}
            <div style={{ padding: "24px 32px" }}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "16px",
                  marginBottom: "20px",
                }}
              >
                {[
                  { label: "Displacement", value: "7,650 kg" },
                  { label: "Draft", value: "2.15 m" },
                  { label: "Sail Area (Main)", value: "42.3 m\u00B2" },
                  { label: "Sail Area (Foresail)", value: "38.7 m\u00B2" },
                  { label: "Owner", value: "J. Thornton" },
                  { label: "Certificate", value: "GBR-2024-1847" },
                ].map((item) => (
                  <div key={item.label}>
                    <div
                      style={{
                        fontFamily: "'Fira Code', monospace",
                        fontSize: "10px",
                        letterSpacing: "0.08em",
                        textTransform: "uppercase",
                        color: COLORS.muted,
                        marginBottom: "4px",
                      }}
                    >
                      {item.label}
                    </div>
                    <div
                      style={{
                        fontFamily: "'Spectral', serif",
                        fontSize: "14px",
                        color: COLORS.text,
                      }}
                    >
                      {item.value}
                    </div>
                  </div>
                ))}
              </div>

              {/* Action row */}
              <div
                style={{
                  display: "flex",
                  gap: "12px",
                  paddingTop: "20px",
                  borderTop: `1px solid ${COLORS.border}`,
                }}
              >
                <button
                  style={{
                    fontFamily: "'Spectral', serif",
                    fontSize: "13px",
                    fontWeight: 500,
                    letterSpacing: "0.04em",
                    color: "white",
                    backgroundColor: COLORS.primary,
                    border: "none",
                    padding: "10px 24px",
                    cursor: "pointer",
                    transition: "background-color 0.3s ease",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = COLORS.secondary)}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = COLORS.primary)}
                >
                  Full Analysis
                </button>
                <button
                  style={{
                    fontFamily: "'Spectral', serif",
                    fontSize: "13px",
                    fontWeight: 500,
                    letterSpacing: "0.04em",
                    color: COLORS.secondary,
                    backgroundColor: "transparent",
                    border: `1px solid ${COLORS.border}`,
                    padding: "10px 24px",
                    cursor: "pointer",
                    transition: "all 0.3s ease",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = COLORS.secondary;
                    e.currentTarget.style.color = COLORS.primary;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = COLORS.border;
                    e.currentTarget.style.color = COLORS.secondary;
                  }}
                >
                  Compare
                </button>
                <button
                  style={{
                    fontFamily: "'Spectral', serif",
                    fontSize: "13px",
                    letterSpacing: "0.04em",
                    color: COLORS.muted,
                    backgroundColor: "transparent",
                    border: "none",
                    padding: "10px 16px",
                    cursor: "pointer",
                    transition: "color 0.3s ease",
                    marginLeft: "auto",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.color = COLORS.primary)}
                  onMouseLeave={(e) => (e.currentTarget.style.color = COLORS.muted)}
                >
                  Certificate PDF
                </button>
              </div>
            </div>
          </div>
        </Section>

        <Divider />

        {/* ── CTA Section ─────────────────────────────────────────────── */}
        <section
          style={{
            position: "relative",
            backgroundColor: COLORS.primary,
            padding: "100px 24px",
            overflow: "hidden",
          }}
        >
          {/* Subtle background texture */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              opacity: 0.03,
              backgroundImage: `radial-gradient(${COLORS.accent} 1px, transparent 1px)`,
              backgroundSize: "24px 24px",
            }}
          />

          <div
            style={{
              position: "relative",
              maxWidth: "640px",
              margin: "0 auto",
              textAlign: "center",
            }}
          >
            <div style={{ marginBottom: "32px", opacity: 0.4 }}>
              <CompassLogo size={40} color={COLORS.background} />
            </div>

            <h2
              style={{
                fontFamily: "'Tenor Sans', sans-serif",
                fontSize: "clamp(1.75rem, 3.5vw, 2.5rem)",
                fontWeight: 400,
                letterSpacing: "0.04em",
                color: COLORS.background,
                lineHeight: 1.25,
                marginBottom: "20px",
              }}
            >
              Begin your analysis.
            </h2>
            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontSize: "16px",
                fontStyle: "italic",
                color: `${COLORS.background}70`,
                lineHeight: 1.65,
                maxWidth: "420px",
                margin: "0 auto 40px",
              }}
            >
              Join over 2,000 competitive sailors who trust Sail Ratings for
              precision insight into IRC and ORC certificates.
            </p>

            {/* CTA search / email */}
            <div
              style={{
                display: "flex",
                maxWidth: "440px",
                margin: "0 auto",
                border: `1px solid ${COLORS.background}20`,
              }}
            >
              <input
                type="email"
                placeholder="you@example.com"
                className="brand-e-cta-input"
                style={{
                  flex: 1,
                  background: `${COLORS.background}0A`,
                  border: "none",
                  outline: "none",
                  fontFamily: "'Spectral', serif",
                  fontSize: "14px",
                  color: COLORS.background,
                  padding: "14px 20px",
                }}
              />
              <button
                style={{
                  fontFamily: "'Spectral', serif",
                  fontSize: "13px",
                  fontWeight: 500,
                  letterSpacing: "0.04em",
                  color: COLORS.primary,
                  backgroundColor: COLORS.accent,
                  border: "none",
                  padding: "14px 28px",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                  transition: "background-color 0.3s ease",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "#C9A430")}
                onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = COLORS.accent)}
              >
                Get Started
              </button>
            </div>

            <p
              style={{
                fontFamily: "'Fira Code', monospace",
                fontSize: "11px",
                color: `${COLORS.background}25`,
                marginTop: "16px",
                letterSpacing: "0.02em",
              }}
            >
              Free lookup &middot; No credit card required
            </p>
          </div>
        </section>

        {/* ── Tone & Brand Principles ─────────────────────────────────── */}
        <Section
          title="Brand Principles"
          subtitle="Three words that govern every design and copy decision."
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: "32px",
            }}
          >
            {[
              {
                word: "Bespoke",
                description:
                  "Every detail is considered. The experience feels tailored, not templated. White space is generous. Nothing is accidental.",
              },
              {
                word: "Discreet",
                description:
                  "Confidence without showmanship. The brand recedes to let the data speak. Colour is muted. Gold appears only when it matters.",
              },
              {
                word: "Expert",
                description:
                  "Authority through precision, not volume. The typography is legible at data-density. The language is specific, never vague.",
              },
            ].map((principle) => (
              <div
                key={principle.word}
                style={{
                  padding: "32px",
                  backgroundColor: "white",
                  border: `1px solid ${COLORS.border}`,
                }}
              >
                <div
                  style={{
                    fontFamily: "'Tenor Sans', sans-serif",
                    fontSize: "20px",
                    fontWeight: 400,
                    letterSpacing: "0.05em",
                    color: COLORS.primary,
                    marginBottom: "16px",
                  }}
                >
                  {principle.word}
                </div>
                <p
                  style={{
                    fontFamily: "'Spectral', serif",
                    fontSize: "14px",
                    color: COLORS.secondary,
                    lineHeight: 1.7,
                  }}
                >
                  {principle.description}
                </p>
              </div>
            ))}
          </div>
        </Section>

        {/* ── Footer ──────────────────────────────────────────────────── */}
        <footer
          style={{
            borderTop: `1px solid ${COLORS.border}`,
            padding: "40px 24px",
            backgroundColor: COLORS.background,
          }}
        >
          <div
            style={{
              maxWidth: "960px",
              margin: "0 auto",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: "16px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <CompassLogo size={24} color={COLORS.muted} />
              <span
                style={{
                  fontFamily: "'Tenor Sans', sans-serif",
                  fontSize: "14px",
                  letterSpacing: "0.05em",
                  color: COLORS.muted,
                }}
              >
                Sail Ratings
              </span>
            </div>
            <p
              style={{
                fontFamily: "'Spectral', serif",
                fontSize: "12px",
                color: COLORS.muted,
                opacity: 0.7,
              }}
            >
              Rating data sourced from public certificates. Not affiliated with the RORC Rating Office or ORC.
            </p>
          </div>
        </footer>
      </div>
    </>
  );
}

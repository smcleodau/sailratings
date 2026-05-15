"use client";

import { useState } from "react";

export default function BrandOptionC() {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);

  const colors = {
    primary: "#1C3A3E",
    secondary: "#A6B1B3",
    accent: "#D97B4F",
    background: "#FFFEF9",
    text: "#2C2C2C",
    muted: "#7A7A7A",
    border: "#EAEAEA",
  };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600;1,700&family=Lato:wght@300;400;700&family=Roboto+Mono:wght@300;400;500&display=swap');

        *, *::before, *::after {
          margin: 0;
          padding: 0;
          box-sizing: border-box;
        }

        html {
          scroll-behavior: smooth;
        }

        body {
          background: ${colors.background};
          color: ${colors.text};
          font-family: 'Lato', sans-serif;
          font-weight: 400;
          font-size: 20px;
          line-height: 1.7;
          -webkit-font-smoothing: antialiased;
          -moz-osx-font-smoothing: grayscale;
        }

        ::selection {
          background: ${colors.accent};
          color: #fff;
        }

        /* Scrollbar styling */
        ::-webkit-scrollbar {
          width: 8px;
        }
        ::-webkit-scrollbar-track {
          background: ${colors.background};
        }
        ::-webkit-scrollbar-thumb {
          background: ${colors.secondary};
          border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
          background: ${colors.primary};
        }

        @keyframes fadeInUp {
          from {
            opacity: 0;
            transform: translateY(30px);
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

        @keyframes slideInLeft {
          from {
            opacity: 0;
            transform: translateX(-40px);
          }
          to {
            opacity: 1;
            transform: translateX(0);
          }
        }

        @keyframes pulseGlow {
          0%, 100% { box-shadow: 0 0 0 0 rgba(217, 123, 79, 0.2); }
          50% { box-shadow: 0 0 0 12px rgba(217, 123, 79, 0); }
        }

        @keyframes shimmer {
          0% { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }

        @keyframes revealLine {
          from { width: 0; }
          to { width: 60px; }
        }
      `}</style>

      <div
        style={{
          minHeight: "100vh",
          background: colors.background,
          fontFamily: "'Lato', sans-serif",
          color: colors.text,
        }}
      >
        {/* === OPTION BAR === */}
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            zIndex: 1000,
            background: colors.primary,
            color: "#fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "10px 32px",
            fontFamily: "'Roboto Mono', monospace",
            fontSize: "12px",
            letterSpacing: "0.12em",
            textTransform: "uppercase",
          }}
        >
          <a
            href="/brand"
            style={{
              color: colors.secondary,
              textDecoration: "none",
              display: "flex",
              alignItems: "center",
              gap: "8px",
              transition: "color 0.3s ease",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#fff")}
            onMouseLeave={(e) => (e.currentTarget.style.color = colors.secondary)}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M10 12L6 8L10 4" />
            </svg>
            All Options
          </a>
          <span style={{ color: "#fff", fontWeight: 500 }}>
            Option C{" "}
            <span style={{ color: colors.accent, margin: "0 6px" }}>/</span>{" "}
            Editorial Premium
          </span>
          <span style={{ color: colors.secondary, fontSize: "11px" }}>
            SailRatings Brand System
          </span>
        </div>

        {/* === HERO SECTION === */}
        <section
          style={{
            position: "relative",
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            overflow: "hidden",
          }}
        >
          {/* Background Image */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              backgroundImage: "url(/hero.jpg)",
              backgroundSize: "cover",
              backgroundPosition: "center 40%",
              filter: "saturate(0.3) brightness(0.35) contrast(1.1)",
            }}
          />
          {/* Warm overlay */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: `linear-gradient(
                170deg,
                rgba(28, 58, 62, 0.85) 0%,
                rgba(28, 58, 62, 0.65) 40%,
                rgba(217, 123, 79, 0.15) 100%
              )`,
            }}
          />
          {/* Vignette */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background:
                "radial-gradient(ellipse at center, transparent 40%, rgba(0,0,0,0.4) 100%)",
            }}
          />
          {/* Fine grain texture overlay */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              opacity: 0.03,
              backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E")`,
              backgroundSize: "128px",
            }}
          />

          {/* Header / Nav */}
          <header
            style={{
              position: "absolute",
              top: "42px",
              left: 0,
              right: 0,
              padding: "28px 48px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              zIndex: 10,
              animation: "fadeIn 1.2s ease-out",
            }}
          >
            {/* Logo / Monogram */}
            <div style={{ display: "flex", alignItems: "center", gap: "18px" }}>
              {/* SR Monogram */}
              <div
                style={{
                  width: "48px",
                  height: "48px",
                  borderRadius: "50%",
                  border: "1.5px solid rgba(255,255,255,0.5)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  position: "relative",
                }}
              >
                <span
                  style={{
                    fontFamily: "'Playfair Display', serif",
                    fontWeight: 600,
                    fontSize: "16px",
                    color: "#fff",
                    letterSpacing: "0.08em",
                  }}
                >
                  SR
                </span>
              </div>
              <div>
                <div
                  style={{
                    fontFamily: "'Playfair Display', serif",
                    fontWeight: 700,
                    fontSize: "22px",
                    color: "#fff",
                    letterSpacing: "0.05em",
                    lineHeight: 1,
                  }}
                >
                  Sail Ratings.
                </div>
                <div
                  style={{
                    fontFamily: "'Roboto Mono', monospace",
                    fontSize: "9px",
                    color: "rgba(255,255,255,0.5)",
                    letterSpacing: "0.25em",
                    textTransform: "uppercase",
                    marginTop: "4px",
                  }}
                >
                  Yacht Racing Intelligence
                </div>
              </div>
            </div>

            {/* Nav Links */}
            <nav style={{ display: "flex", alignItems: "center", gap: "36px" }}>
              {["Ratings", "Analysis", "Fleet", "Journal"].map((item) => (
                <a
                  key={item}
                  href="#"
                  style={{
                    color: "rgba(255,255,255,0.7)",
                    textDecoration: "none",
                    fontFamily: "'Lato', sans-serif",
                    fontWeight: 300,
                    fontSize: "14px",
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                    transition: "color 0.3s ease",
                    position: "relative",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.color = "#fff")}
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.color = "rgba(255,255,255,0.7)")
                  }
                >
                  {item}
                </a>
              ))}
              <a
                href="#"
                style={{
                  color: colors.accent,
                  textDecoration: "none",
                  fontFamily: "'Roboto Mono', monospace",
                  fontWeight: 500,
                  fontSize: "12px",
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  padding: "10px 24px",
                  border: `1px solid ${colors.accent}`,
                  transition: "all 0.3s ease",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = colors.accent;
                  e.currentTarget.style.color = "#fff";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.color = colors.accent;
                }}
              >
                Subscribe
              </a>
            </nav>
          </header>

          {/* Hero Content */}
          <div
            style={{
              position: "relative",
              zIndex: 5,
              textAlign: "center",
              maxWidth: "860px",
              padding: "0 32px",
              marginTop: "40px",
            }}
          >
            {/* Decorative top rule */}
            <div
              style={{
                width: "60px",
                height: "1px",
                background: colors.accent,
                margin: "0 auto 32px",
                animation: "revealLine 1s ease-out 0.3s both",
              }}
            />
            {/* Kicker */}
            <div
              style={{
                fontFamily: "'Roboto Mono', monospace",
                fontSize: "11px",
                letterSpacing: "0.3em",
                textTransform: "uppercase",
                color: colors.accent,
                marginBottom: "28px",
                animation: "fadeInUp 0.8s ease-out 0.5s both",
              }}
            >
              Performance Data &middot; Race Analytics &middot; Fleet Intelligence
            </div>

            {/* Headline */}
            <h1
              style={{
                fontFamily: "'Playfair Display', serif",
                fontWeight: 700,
                fontSize: "clamp(2.5rem, 4vw, 4rem)",
                lineHeight: 1.15,
                color: "#fff",
                letterSpacing: "0.02em",
                marginBottom: "28px",
                animation: "fadeInUp 0.9s ease-out 0.7s both",
              }}
            >
              The art and science of
              <br />
              <span style={{ fontStyle: "italic", color: colors.accent }}>
                competitive advantage.
              </span>
            </h1>

            {/* Subheadline */}
            <p
              style={{
                fontFamily: "'Lato', sans-serif",
                fontWeight: 300,
                fontSize: "20px",
                lineHeight: 1.8,
                color: "rgba(255,255,255,0.7)",
                maxWidth: "580px",
                margin: "0 auto 48px",
                animation: "fadeInUp 0.9s ease-out 0.9s both",
              }}
            >
              Uncover hidden patterns in yacht racing performance. Our editorial-grade
              analysis transforms raw data into decisive intelligence.
            </p>

            {/* Search Bar */}
            <div
              style={{
                maxWidth: "560px",
                margin: "0 auto",
                animation: "fadeInUp 0.9s ease-out 1.1s both",
              }}
            >
              <div
                style={{
                  position: "relative",
                  background: "rgba(255,255,255,0.08)",
                  backdropFilter: "blur(20px)",
                  WebkitBackdropFilter: "blur(20px)",
                  border: `1px solid ${
                    searchFocused
                      ? "rgba(217,123,79,0.6)"
                      : "rgba(255,255,255,0.15)"
                  }`,
                  borderRadius: "2px",
                  transition: "all 0.4s ease",
                  boxShadow: searchFocused
                    ? "0 0 0 4px rgba(217,123,79,0.1), 0 20px 60px rgba(0,0,0,0.3)"
                    : "0 20px 60px rgba(0,0,0,0.2)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "4px 6px 4px 20px",
                  }}
                >
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="rgba(255,255,255,0.4)"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    style={{ flexShrink: 0 }}
                  >
                    <circle cx="11" cy="11" r="8" />
                    <path d="M21 21l-4.35-4.35" />
                  </svg>
                  <input
                    type="text"
                    placeholder="Search boats, classes, or regattas..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onFocus={() => setSearchFocused(true)}
                    onBlur={() => setSearchFocused(false)}
                    style={{
                      flex: 1,
                      border: "none",
                      outline: "none",
                      background: "transparent",
                      color: "#fff",
                      fontFamily: "'Lato', sans-serif",
                      fontWeight: 300,
                      fontSize: "15px",
                      padding: "16px 16px",
                      letterSpacing: "0.02em",
                    }}
                  />
                  <button
                    style={{
                      background: colors.accent,
                      border: "none",
                      color: "#fff",
                      fontFamily: "'Roboto Mono', monospace",
                      fontSize: "11px",
                      fontWeight: 500,
                      letterSpacing: "0.15em",
                      textTransform: "uppercase",
                      padding: "14px 28px",
                      cursor: "pointer",
                      transition: "all 0.3s ease",
                    }}
                    onMouseEnter={(e) =>
                      (e.currentTarget.style.background = "#c46a3f")
                    }
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.background = colors.accent)
                    }
                  >
                    Search
                  </button>
                </div>
              </div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "center",
                  gap: "24px",
                  marginTop: "16px",
                  fontFamily: "'Roboto Mono', monospace",
                  fontSize: "10px",
                  letterSpacing: "0.15em",
                  textTransform: "uppercase",
                  color: "rgba(255,255,255,0.35)",
                }}
              >
                <span>IRC &middot; ORC &middot; ORR &middot; HPR</span>
              </div>
            </div>
          </div>

          {/* Scroll indicator */}
          <div
            style={{
              position: "absolute",
              bottom: "40px",
              left: "50%",
              transform: "translateX(-50%)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "8px",
              animation: "fadeIn 1s ease-out 2s both",
              zIndex: 5,
            }}
          >
            <span
              style={{
                fontFamily: "'Roboto Mono', monospace",
                fontSize: "9px",
                letterSpacing: "0.3em",
                textTransform: "uppercase",
                color: "rgba(255,255,255,0.3)",
              }}
            >
              Explore
            </span>
            <svg
              width="16"
              height="24"
              viewBox="0 0 16 24"
              fill="none"
              stroke="rgba(255,255,255,0.3)"
              strokeWidth="1"
            >
              <rect x="4" y="1" width="8" height="14" rx="4" />
              <circle cx="8" cy="6" r="1.5" fill="rgba(255,255,255,0.3)" />
              <path d="M8 18L8 22M5 20L8 23L11 20" />
            </svg>
          </div>
        </section>

        {/* === COLOR PALETTE === */}
        <section
          style={{
            padding: "120px 48px",
            maxWidth: "1200px",
            margin: "0 auto",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "80px",
              flexWrap: "wrap",
            }}
          >
            <div style={{ flex: "0 0 280px" }}>
              <div
                style={{
                  fontFamily: "'Roboto Mono', monospace",
                  fontSize: "10px",
                  letterSpacing: "0.3em",
                  textTransform: "uppercase",
                  color: colors.accent,
                  marginBottom: "16px",
                }}
              >
                01 / Color System
              </div>
              <h2
                style={{
                  fontFamily: "'Playfair Display', serif",
                  fontWeight: 600,
                  fontSize: "32px",
                  lineHeight: 1.3,
                  color: colors.primary,
                  marginBottom: "20px",
                }}
              >
                A palette drawn
                <br />
                from the sea.
              </h2>
              <p
                style={{
                  fontFamily: "'Lato', sans-serif",
                  fontWeight: 300,
                  fontSize: "16px",
                  lineHeight: 1.8,
                  color: colors.muted,
                }}
              >
                Rooted in the deep tones of Baltic waters and weathered
                teak, each color carries intention and restraint.
              </p>
            </div>

            <div style={{ flex: 1, minWidth: "500px" }}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: "16px",
                }}
              >
                {[
                  {
                    name: "Deep Baltic\nPine Green",
                    hex: colors.primary,
                    role: "Primary",
                    light: true,
                  },
                  {
                    name: "Seafoam\nGrey",
                    hex: colors.secondary,
                    role: "Secondary",
                    light: false,
                  },
                  {
                    name: "Terracotta",
                    hex: colors.accent,
                    role: "Accent",
                    light: true,
                  },
                  {
                    name: "Warm\nAlabaster",
                    hex: colors.background,
                    role: "Background",
                    light: false,
                  },
                  {
                    name: "Soft\nCharcoal",
                    hex: colors.text,
                    role: "Text",
                    light: true,
                  },
                  {
                    name: "Muted",
                    hex: colors.muted,
                    role: "Muted",
                    light: true,
                  },
                  {
                    name: "Border",
                    hex: colors.border,
                    role: "Border",
                    light: false,
                  },
                ].map((c) => (
                  <div key={c.hex + c.role}>
                    <div
                      style={{
                        width: "100%",
                        aspectRatio: "1",
                        background: c.hex,
                        borderRadius: "2px",
                        border:
                          c.hex === colors.background || c.hex === colors.border
                            ? `1px solid ${colors.border}`
                            : "none",
                        marginBottom: "12px",
                        position: "relative",
                        overflow: "hidden",
                        transition: "transform 0.3s ease",
                        cursor: "default",
                      }}
                    >
                      <div
                        style={{
                          position: "absolute",
                          bottom: "10px",
                          left: "10px",
                          fontFamily: "'Roboto Mono', monospace",
                          fontSize: "10px",
                          letterSpacing: "0.05em",
                          color: c.light ? "rgba(255,255,255,0.7)" : "rgba(0,0,0,0.4)",
                        }}
                      >
                        {c.hex}
                      </div>
                    </div>
                    <div
                      style={{
                        fontFamily: "'Roboto Mono', monospace",
                        fontSize: "10px",
                        letterSpacing: "0.1em",
                        textTransform: "uppercase",
                        color: colors.accent,
                        marginBottom: "4px",
                      }}
                    >
                      {c.role}
                    </div>
                    <div
                      style={{
                        fontFamily: "'Lato', sans-serif",
                        fontWeight: 400,
                        fontSize: "13px",
                        color: colors.text,
                        lineHeight: 1.4,
                        whiteSpace: "pre-line",
                      }}
                    >
                      {c.name}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* Divider */}
        <div
          style={{
            maxWidth: "1200px",
            margin: "0 auto",
            padding: "0 48px",
          }}
        >
          <div style={{ height: "1px", background: colors.border }} />
        </div>

        {/* === TYPOGRAPHY === */}
        <section
          style={{
            padding: "120px 48px",
            maxWidth: "1200px",
            margin: "0 auto",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "80px",
              flexWrap: "wrap",
            }}
          >
            <div style={{ flex: "0 0 280px" }}>
              <div
                style={{
                  fontFamily: "'Roboto Mono', monospace",
                  fontSize: "10px",
                  letterSpacing: "0.3em",
                  textTransform: "uppercase",
                  color: colors.accent,
                  marginBottom: "16px",
                }}
              >
                02 / Typography
              </div>
              <h2
                style={{
                  fontFamily: "'Playfair Display', serif",
                  fontWeight: 600,
                  fontSize: "32px",
                  lineHeight: 1.3,
                  color: colors.primary,
                  marginBottom: "20px",
                }}
              >
                Three voices,
                <br />
                one language.
              </h2>
              <p
                style={{
                  fontFamily: "'Lato', sans-serif",
                  fontWeight: 300,
                  fontSize: "16px",
                  lineHeight: 1.8,
                  color: colors.muted,
                }}
              >
                Display, body, and data each have their own typeface, tuned for
                clarity across every context.
              </p>
            </div>

            <div style={{ flex: 1, minWidth: "500px" }}>
              {/* Playfair Display */}
              <div style={{ marginBottom: "56px" }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    marginBottom: "20px",
                    borderBottom: `1px solid ${colors.border}`,
                    paddingBottom: "12px",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "'Roboto Mono', monospace",
                      fontSize: "10px",
                      letterSpacing: "0.2em",
                      textTransform: "uppercase",
                      color: colors.muted,
                    }}
                  >
                    Display / Playfair Display
                  </span>
                  <span
                    style={{
                      fontFamily: "'Roboto Mono', monospace",
                      fontSize: "10px",
                      letterSpacing: "0.1em",
                      color: colors.secondary,
                    }}
                  >
                    500 &ndash; 700
                  </span>
                </div>
                <div
                  style={{
                    fontFamily: "'Playfair Display', serif",
                    fontWeight: 700,
                    fontSize: "48px",
                    lineHeight: 1.2,
                    color: colors.primary,
                    marginBottom: "12px",
                    letterSpacing: "0.02em",
                  }}
                >
                  Sail Ratings.
                </div>
                <div
                  style={{
                    fontFamily: "'Playfair Display', serif",
                    fontWeight: 500,
                    fontSize: "28px",
                    lineHeight: 1.4,
                    color: colors.text,
                    fontStyle: "italic",
                  }}
                >
                  Where precision meets the open water.
                </div>
              </div>

              {/* Lato */}
              <div style={{ marginBottom: "56px" }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    marginBottom: "20px",
                    borderBottom: `1px solid ${colors.border}`,
                    paddingBottom: "12px",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "'Roboto Mono', monospace",
                      fontSize: "10px",
                      letterSpacing: "0.2em",
                      textTransform: "uppercase",
                      color: colors.muted,
                    }}
                  >
                    Body / Lato
                  </span>
                  <span
                    style={{
                      fontFamily: "'Roboto Mono', monospace",
                      fontSize: "10px",
                      letterSpacing: "0.1em",
                      color: colors.secondary,
                    }}
                  >
                    300 &ndash; 400
                  </span>
                </div>
                <p
                  style={{
                    fontFamily: "'Lato', sans-serif",
                    fontWeight: 300,
                    fontSize: "20px",
                    lineHeight: 1.8,
                    color: colors.text,
                    marginBottom: "16px",
                  }}
                >
                  Body text is set in Lato Light at 20px for a luxurious, spacious
                  reading experience. Each paragraph breathes with generous line
                  height, inviting the reader to linger.
                </p>
                <p
                  style={{
                    fontFamily: "'Lato', sans-serif",
                    fontWeight: 400,
                    fontSize: "16px",
                    lineHeight: 1.7,
                    color: colors.muted,
                  }}
                >
                  Supporting text uses regular weight at smaller sizes for
                  captions, metadata, and secondary information that should be
                  present but not dominant.
                </p>
              </div>

              {/* Roboto Mono */}
              <div>
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    marginBottom: "20px",
                    borderBottom: `1px solid ${colors.border}`,
                    paddingBottom: "12px",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "'Roboto Mono', monospace",
                      fontSize: "10px",
                      letterSpacing: "0.2em",
                      textTransform: "uppercase",
                      color: colors.muted,
                    }}
                  >
                    Data / Roboto Mono
                  </span>
                  <span
                    style={{
                      fontFamily: "'Roboto Mono', monospace",
                      fontSize: "10px",
                      letterSpacing: "0.1em",
                      color: colors.secondary,
                    }}
                  >
                    300 &ndash; 500
                  </span>
                </div>
                <div
                  style={{
                    display: "flex",
                    gap: "48px",
                    flexWrap: "wrap",
                  }}
                >
                  <div>
                    <div
                      style={{
                        fontFamily: "'Roboto Mono', monospace",
                        fontWeight: 500,
                        fontSize: "36px",
                        color: colors.primary,
                        marginBottom: "4px",
                      }}
                    >
                      1.0847
                    </div>
                    <div
                      style={{
                        fontFamily: "'Roboto Mono', monospace",
                        fontWeight: 300,
                        fontSize: "11px",
                        letterSpacing: "0.15em",
                        textTransform: "uppercase",
                        color: colors.muted,
                      }}
                    >
                      TCC Rating
                    </div>
                  </div>
                  <div>
                    <div
                      style={{
                        fontFamily: "'Roboto Mono', monospace",
                        fontWeight: 500,
                        fontSize: "36px",
                        color: colors.accent,
                        marginBottom: "4px",
                      }}
                    >
                      +2.3%
                    </div>
                    <div
                      style={{
                        fontFamily: "'Roboto Mono', monospace",
                        fontWeight: 300,
                        fontSize: "11px",
                        letterSpacing: "0.15em",
                        textTransform: "uppercase",
                        color: colors.muted,
                      }}
                    >
                      YoY Change
                    </div>
                  </div>
                  <div>
                    <div
                      style={{
                        fontFamily: "'Roboto Mono', monospace",
                        fontWeight: 500,
                        fontSize: "36px",
                        color: colors.primary,
                        marginBottom: "4px",
                      }}
                    >
                      847
                    </div>
                    <div
                      style={{
                        fontFamily: "'Roboto Mono', monospace",
                        fontWeight: 300,
                        fontSize: "11px",
                        letterSpacing: "0.15em",
                        textTransform: "uppercase",
                        color: colors.muted,
                      }}
                    >
                      Fleet Rank
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Divider */}
        <div
          style={{
            maxWidth: "1200px",
            margin: "0 auto",
            padding: "0 48px",
          }}
        >
          <div style={{ height: "1px", background: colors.border }} />
        </div>

        {/* === BOAT CARD === */}
        <section
          style={{
            padding: "120px 48px",
            maxWidth: "1200px",
            margin: "0 auto",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "80px",
              flexWrap: "wrap",
            }}
          >
            <div style={{ flex: "0 0 280px" }}>
              <div
                style={{
                  fontFamily: "'Roboto Mono', monospace",
                  fontSize: "10px",
                  letterSpacing: "0.3em",
                  textTransform: "uppercase",
                  color: colors.accent,
                  marginBottom: "16px",
                }}
              >
                03 / Boat Card
              </div>
              <h2
                style={{
                  fontFamily: "'Playfair Display', serif",
                  fontWeight: 600,
                  fontSize: "32px",
                  lineHeight: 1.3,
                  color: colors.primary,
                  marginBottom: "20px",
                }}
              >
                Data, elevated
                <br />
                to craft.
              </h2>
              <p
                style={{
                  fontFamily: "'Lato', sans-serif",
                  fontWeight: 300,
                  fontSize: "16px",
                  lineHeight: 1.8,
                  color: colors.muted,
                }}
              >
                Every rating card is a distilled portrait of performance.
                Numbers are given room to speak.
              </p>
            </div>

            <div style={{ flex: 1, minWidth: "500px" }}>
              {/* Boat Card */}
              <div
                style={{
                  background: "#fff",
                  border: `1px solid ${colors.border}`,
                  borderRadius: "2px",
                  overflow: "hidden",
                  maxWidth: "600px",
                  transition: "box-shadow 0.4s ease, transform 0.4s ease",
                  cursor: "default",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.boxShadow =
                    "0 24px 80px rgba(28,58,62,0.1)";
                  e.currentTarget.style.transform = "translateY(-2px)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.boxShadow = "none";
                  e.currentTarget.style.transform = "translateY(0)";
                }}
              >
                {/* Card header band */}
                <div
                  style={{
                    background: colors.primary,
                    padding: "20px 28px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                  }}
                >
                  <div>
                    <div
                      style={{
                        fontFamily: "'Playfair Display', serif",
                        fontWeight: 700,
                        fontSize: "22px",
                        color: "#fff",
                        letterSpacing: "0.03em",
                        marginBottom: "2px",
                      }}
                    >
                      Bella Mente
                    </div>
                    <div
                      style={{
                        fontFamily: "'Roboto Mono', monospace",
                        fontSize: "10px",
                        letterSpacing: "0.2em",
                        textTransform: "uppercase",
                        color: "rgba(255,255,255,0.5)",
                      }}
                    >
                      Judel / Vrolijk 72 &middot; USA 45299
                    </div>
                  </div>
                  <div
                    style={{
                      fontFamily: "'Roboto Mono', monospace",
                      fontWeight: 500,
                      fontSize: "11px",
                      letterSpacing: "0.15em",
                      textTransform: "uppercase",
                      color: colors.accent,
                      padding: "6px 14px",
                      border: `1px solid rgba(217,123,79,0.4)`,
                      borderRadius: "1px",
                    }}
                  >
                    IRC
                  </div>
                </div>

                {/* Card body */}
                <div style={{ padding: "28px" }}>
                  {/* Rating stats row */}
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(4, 1fr)",
                      gap: "20px",
                      marginBottom: "28px",
                    }}
                  >
                    {[
                      { label: "TCC", value: "1.4731", highlight: true },
                      { label: "GPH", value: "478.2", highlight: false },
                      { label: "TMF", value: "0.9612", highlight: false },
                      { label: "CDL", value: "22.14m", highlight: false },
                    ].map((stat) => (
                      <div key={stat.label}>
                        <div
                          style={{
                            fontFamily: "'Roboto Mono', monospace",
                            fontWeight: 300,
                            fontSize: "9px",
                            letterSpacing: "0.2em",
                            textTransform: "uppercase",
                            color: colors.muted,
                            marginBottom: "6px",
                          }}
                        >
                          {stat.label}
                        </div>
                        <div
                          style={{
                            fontFamily: "'Roboto Mono', monospace",
                            fontWeight: 500,
                            fontSize: "22px",
                            color: stat.highlight
                              ? colors.accent
                              : colors.primary,
                          }}
                        >
                          {stat.value}
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Divider */}
                  <div
                    style={{
                      height: "1px",
                      background: colors.border,
                      marginBottom: "24px",
                    }}
                  />

                  {/* Details row */}
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <div style={{ display: "flex", gap: "32px" }}>
                      {[
                        { label: "Class", value: "Mini Maxi" },
                        { label: "Year", value: "2016" },
                        { label: "LOA", value: "72 ft" },
                      ].map((item) => (
                        <div key={item.label}>
                          <div
                            style={{
                              fontFamily: "'Roboto Mono', monospace",
                              fontSize: "9px",
                              letterSpacing: "0.2em",
                              textTransform: "uppercase",
                              color: colors.muted,
                              marginBottom: "4px",
                            }}
                          >
                            {item.label}
                          </div>
                          <div
                            style={{
                              fontFamily: "'Lato', sans-serif",
                              fontWeight: 400,
                              fontSize: "14px",
                              color: colors.text,
                            }}
                          >
                            {item.value}
                          </div>
                        </div>
                      ))}
                    </div>

                    <button
                      style={{
                        background: "transparent",
                        border: `1px solid ${colors.primary}`,
                        color: colors.primary,
                        fontFamily: "'Roboto Mono', monospace",
                        fontSize: "10px",
                        fontWeight: 500,
                        letterSpacing: "0.15em",
                        textTransform: "uppercase",
                        padding: "10px 24px",
                        cursor: "pointer",
                        transition: "all 0.3s ease",
                        borderRadius: "1px",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = colors.primary;
                        e.currentTarget.style.color = "#fff";
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = "transparent";
                        e.currentTarget.style.color = colors.primary;
                      }}
                    >
                      View Full Report
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Divider */}
        <div
          style={{
            maxWidth: "1200px",
            margin: "0 auto",
            padding: "0 48px",
          }}
        >
          <div style={{ height: "1px", background: colors.border }} />
        </div>

        {/* === SEARCH COMPONENT === */}
        <section
          style={{
            padding: "120px 48px",
            maxWidth: "1200px",
            margin: "0 auto",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "80px",
              flexWrap: "wrap",
            }}
          >
            <div style={{ flex: "0 0 280px" }}>
              <div
                style={{
                  fontFamily: "'Roboto Mono', monospace",
                  fontSize: "10px",
                  letterSpacing: "0.3em",
                  textTransform: "uppercase",
                  color: colors.accent,
                  marginBottom: "16px",
                }}
              >
                04 / Search
              </div>
              <h2
                style={{
                  fontFamily: "'Playfair Display', serif",
                  fontWeight: 600,
                  fontSize: "32px",
                  lineHeight: 1.3,
                  color: colors.primary,
                  marginBottom: "20px",
                }}
              >
                Find with
                <br />
                precision.
              </h2>
              <p
                style={{
                  fontFamily: "'Lato', sans-serif",
                  fontWeight: 300,
                  fontSize: "16px",
                  lineHeight: 1.8,
                  color: colors.muted,
                }}
              >
                The search experience is minimal and focused. No clutter,
                just clarity.
              </p>
            </div>

            <div style={{ flex: 1, minWidth: "500px" }}>
              {/* Standalone search on light background */}
              <div
                style={{
                  background: "#fff",
                  border: `1px solid ${colors.border}`,
                  borderRadius: "2px",
                  padding: "40px",
                  maxWidth: "600px",
                }}
              >
                <div style={{ marginBottom: "24px" }}>
                  <div
                    style={{
                      fontFamily: "'Playfair Display', serif",
                      fontWeight: 600,
                      fontSize: "20px",
                      color: colors.primary,
                      marginBottom: "6px",
                    }}
                  >
                    Search the Fleet
                  </div>
                  <div
                    style={{
                      fontFamily: "'Lato', sans-serif",
                      fontWeight: 300,
                      fontSize: "14px",
                      color: colors.muted,
                    }}
                  >
                    Enter a boat name, sail number, or class to begin.
                  </div>
                </div>

                <div
                  style={{
                    display: "flex",
                    gap: "0",
                    border: `1px solid ${colors.border}`,
                    borderRadius: "2px",
                    overflow: "hidden",
                    transition: "border-color 0.3s ease",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      padding: "0 16px",
                      background: colors.background,
                      borderRight: `1px solid ${colors.border}`,
                    }}
                  >
                    <svg
                      width="16"
                      height="16"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke={colors.muted}
                      strokeWidth="1.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <circle cx="11" cy="11" r="8" />
                      <path d="M21 21l-4.35-4.35" />
                    </svg>
                  </div>
                  <input
                    type="text"
                    placeholder="e.g. Rambler 88"
                    style={{
                      flex: 1,
                      border: "none",
                      outline: "none",
                      background: "#fff",
                      fontFamily: "'Lato', sans-serif",
                      fontWeight: 300,
                      fontSize: "15px",
                      padding: "16px",
                      color: colors.text,
                    }}
                  />
                  <button
                    style={{
                      background: colors.primary,
                      border: "none",
                      color: "#fff",
                      fontFamily: "'Roboto Mono', monospace",
                      fontSize: "11px",
                      fontWeight: 500,
                      letterSpacing: "0.15em",
                      textTransform: "uppercase",
                      padding: "16px 28px",
                      cursor: "pointer",
                      transition: "background 0.3s ease",
                    }}
                    onMouseEnter={(e) =>
                      (e.currentTarget.style.background = "#264a4f")
                    }
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.background = colors.primary)
                    }
                  >
                    Search
                  </button>
                </div>

                {/* Filter chips */}
                <div
                  style={{
                    display: "flex",
                    gap: "8px",
                    marginTop: "16px",
                    flexWrap: "wrap",
                  }}
                >
                  {["All Ratings", "IRC", "ORC", "ORR", "HPR"].map((f, i) => (
                    <button
                      key={f}
                      style={{
                        background: i === 0 ? colors.primary : "transparent",
                        color: i === 0 ? "#fff" : colors.muted,
                        border: `1px solid ${
                          i === 0 ? colors.primary : colors.border
                        }`,
                        fontFamily: "'Roboto Mono', monospace",
                        fontSize: "10px",
                        fontWeight: 400,
                        letterSpacing: "0.1em",
                        textTransform: "uppercase",
                        padding: "8px 16px",
                        cursor: "pointer",
                        borderRadius: "1px",
                        transition: "all 0.3s ease",
                      }}
                    >
                      {f}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* === CTA SECTION === */}
        <section
          style={{
            position: "relative",
            padding: "0",
            marginTop: "40px",
          }}
        >
          {/* Full-width CTA with dark background */}
          <div
            style={{
              background: colors.primary,
              position: "relative",
              overflow: "hidden",
            }}
          >
            {/* Subtle texture */}
            <div
              style={{
                position: "absolute",
                inset: 0,
                opacity: 0.04,
                backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E")`,
                backgroundSize: "128px",
              }}
            />
            {/* Gradient accent line at top */}
            <div
              style={{
                height: "3px",
                background: `linear-gradient(90deg, transparent 0%, ${colors.accent} 30%, ${colors.accent} 70%, transparent 100%)`,
              }}
            />

            <div
              style={{
                position: "relative",
                maxWidth: "900px",
                margin: "0 auto",
                padding: "100px 48px",
                textAlign: "center",
              }}
            >
              {/* Monogram */}
              <div
                style={{
                  width: "64px",
                  height: "64px",
                  borderRadius: "50%",
                  border: "1px solid rgba(255,255,255,0.2)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  margin: "0 auto 40px",
                }}
              >
                <span
                  style={{
                    fontFamily: "'Playfair Display', serif",
                    fontWeight: 600,
                    fontSize: "20px",
                    color: "rgba(255,255,255,0.8)",
                    letterSpacing: "0.08em",
                  }}
                >
                  SR
                </span>
              </div>

              <h2
                style={{
                  fontFamily: "'Playfair Display', serif",
                  fontWeight: 700,
                  fontSize: "clamp(2rem, 3.5vw, 3.2rem)",
                  lineHeight: 1.2,
                  color: "#fff",
                  letterSpacing: "0.02em",
                  marginBottom: "24px",
                }}
              >
                Intelligence that
                <br />
                <span style={{ fontStyle: "italic", color: colors.accent }}>
                  wins races.
                </span>
              </h2>

              <p
                style={{
                  fontFamily: "'Lato', sans-serif",
                  fontWeight: 300,
                  fontSize: "18px",
                  lineHeight: 1.8,
                  color: "rgba(255,255,255,0.6)",
                  maxWidth: "520px",
                  margin: "0 auto 48px",
                }}
              >
                Join the world&apos;s most discerning yacht racing teams.
                Access comprehensive rating analysis, historical trends,
                and competitive insights curated by experts.
              </p>

              <div
                style={{
                  display: "flex",
                  justifyContent: "center",
                  gap: "16px",
                  flexWrap: "wrap",
                }}
              >
                <button
                  style={{
                    background: colors.accent,
                    border: `1px solid ${colors.accent}`,
                    color: "#fff",
                    fontFamily: "'Roboto Mono', monospace",
                    fontSize: "12px",
                    fontWeight: 500,
                    letterSpacing: "0.15em",
                    textTransform: "uppercase",
                    padding: "18px 44px",
                    cursor: "pointer",
                    transition: "all 0.3s ease",
                    borderRadius: "1px",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "#c46a3f";
                    e.currentTarget.style.borderColor = "#c46a3f";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = colors.accent;
                    e.currentTarget.style.borderColor = colors.accent;
                  }}
                >
                  Start Free Trial
                </button>
                <button
                  style={{
                    background: "transparent",
                    border: "1px solid rgba(255,255,255,0.25)",
                    color: "rgba(255,255,255,0.7)",
                    fontFamily: "'Roboto Mono', monospace",
                    fontSize: "12px",
                    fontWeight: 400,
                    letterSpacing: "0.15em",
                    textTransform: "uppercase",
                    padding: "18px 44px",
                    cursor: "pointer",
                    transition: "all 0.3s ease",
                    borderRadius: "1px",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = "rgba(255,255,255,0.6)";
                    e.currentTarget.style.color = "#fff";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor =
                      "rgba(255,255,255,0.25)";
                    e.currentTarget.style.color = "rgba(255,255,255,0.7)";
                  }}
                >
                  View Sample Report
                </button>
              </div>

              {/* Trust line */}
              <div
                style={{
                  marginTop: "56px",
                  display: "flex",
                  justifyContent: "center",
                  gap: "40px",
                  flexWrap: "wrap",
                }}
              >
                {[
                  "12,400+ Boats Rated",
                  "47 Countries",
                  "Updated Weekly",
                ].map((stat) => (
                  <div
                    key={stat}
                    style={{
                      fontFamily: "'Roboto Mono', monospace",
                      fontSize: "10px",
                      letterSpacing: "0.2em",
                      textTransform: "uppercase",
                      color: "rgba(255,255,255,0.3)",
                    }}
                  >
                    {stat}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* === TONE & BRAND SUMMARY === */}
        <section
          style={{
            padding: "100px 48px 60px",
            maxWidth: "1200px",
            margin: "0 auto",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "80px",
              flexWrap: "wrap",
            }}
          >
            <div style={{ flex: "0 0 280px" }}>
              <div
                style={{
                  fontFamily: "'Roboto Mono', monospace",
                  fontSize: "10px",
                  letterSpacing: "0.3em",
                  textTransform: "uppercase",
                  color: colors.accent,
                  marginBottom: "16px",
                }}
              >
                05 / Brand Tone
              </div>
              <h2
                style={{
                  fontFamily: "'Playfair Display', serif",
                  fontWeight: 600,
                  fontSize: "32px",
                  lineHeight: 1.3,
                  color: colors.primary,
                  marginBottom: "20px",
                }}
              >
                The editorial
                <br />
                voice.
              </h2>
            </div>

            <div style={{ flex: 1, minWidth: "500px" }}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: "32px",
                }}
              >
                {[
                  {
                    word: "Sophisticated",
                    desc: "Every detail reflects considered taste. Nothing extraneous, nothing arbitrary.",
                  },
                  {
                    word: "Curated",
                    desc: "Information is selected, not dumped. We edit reality into meaning.",
                  },
                  {
                    word: "Intelligent",
                    desc: "Data speaks with authority. Analysis is sharp, conclusions are earned.",
                  },
                ].map((tone) => (
                  <div
                    key={tone.word}
                    style={{
                      padding: "28px",
                      border: `1px solid ${colors.border}`,
                      borderRadius: "2px",
                      transition: "border-color 0.3s ease",
                    }}
                    onMouseEnter={(e) =>
                      (e.currentTarget.style.borderColor = colors.accent)
                    }
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.borderColor = colors.border)
                    }
                  >
                    <div
                      style={{
                        fontFamily: "'Playfair Display', serif",
                        fontWeight: 600,
                        fontSize: "20px",
                        color: colors.primary,
                        marginBottom: "12px",
                        fontStyle: "italic",
                      }}
                    >
                      {tone.word}
                    </div>
                    <p
                      style={{
                        fontFamily: "'Lato', sans-serif",
                        fontWeight: 300,
                        fontSize: "14px",
                        lineHeight: 1.7,
                        color: colors.muted,
                      }}
                    >
                      {tone.desc}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* === FOOTER === */}
        <footer
          style={{
            borderTop: `1px solid ${colors.border}`,
            padding: "48px",
            maxWidth: "1200px",
            margin: "0 auto",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
            <div
              style={{
                width: "36px",
                height: "36px",
                borderRadius: "50%",
                border: `1px solid ${colors.border}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <span
                style={{
                  fontFamily: "'Playfair Display', serif",
                  fontWeight: 600,
                  fontSize: "12px",
                  color: colors.primary,
                  letterSpacing: "0.05em",
                }}
              >
                SR
              </span>
            </div>
            <span
              style={{
                fontFamily: "'Playfair Display', serif",
                fontWeight: 700,
                fontSize: "16px",
                color: colors.primary,
                letterSpacing: "0.05em",
              }}
            >
              Sail Ratings.
            </span>
          </div>
          <div
            style={{
              fontFamily: "'Roboto Mono', monospace",
              fontSize: "10px",
              letterSpacing: "0.15em",
              textTransform: "uppercase",
              color: colors.muted,
            }}
          >
            Option C &middot; Editorial Premium &middot; Brand Preview
          </div>
          <div
            style={{
              fontFamily: "'Lato', sans-serif",
              fontWeight: 300,
              fontSize: "13px",
              color: colors.muted,
            }}
          >
            &copy; 2026 Sail Ratings. All rights reserved.
          </div>
        </footer>

        {/* Bottom spacer for fixed option bar */}
        <div style={{ height: "20px" }} />
      </div>
    </>
  );
}

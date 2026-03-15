import Image from "next/image";

export default function Hero() {
  return (
    <section className="relative w-full h-screen min-h-[600px] overflow-hidden">
      {/* Background image with vintage filter */}
      <div className="absolute inset-0 vintage-photo">
        <Image
          src="/hero.jpg"
          alt="Racing yachts competing under sail"
          fill
          priority
          className="object-cover"
          sizes="100vw"
          quality={85}
        />
      </div>

      {/* Navigation bar */}
      <nav className="absolute top-0 left-0 right-0 z-30 px-8 py-6 flex items-center justify-between">
        <span
          className="brand-wordmark text-white text-base sm:text-lg animate-fade-in-up"
          style={{ textShadow: "0 2px 12px rgba(0,0,0,0.3)" }}
        >
          Sail Ratings
        </span>
      </nav>

      {/* Content — centered vertically */}
      <div className="relative z-20 flex flex-col items-center justify-center h-full px-6 text-center pb-28">
        <h1
          className="heading-serif-bold text-white text-5xl sm:text-6xl md:text-7xl lg:text-8xl max-w-5xl mb-8 italic animate-fade-in-up animation-delay-200"
          style={{ textShadow: "0 4px 30px rgba(0,0,0,0.4), 0 1px 3px rgba(0,0,0,0.3)" }}
        >
          What is your rating <em>really</em> costing you?
        </h1>
        <p
          className="body-text text-white/85 text-lg sm:text-xl md:text-2xl max-w-2xl font-light mb-2 animate-fade-in-up animation-delay-400"
          style={{ textShadow: "0 1px 10px rgba(0,0,0,0.25)" }}
        >
          6,197 boats. 31,000 race results. We know where your points are hiding.
        </p>
      </div>

      {/* Trust bar at bottom */}
      <div className="absolute bottom-16 left-0 right-0 z-20 flex justify-center animate-fade-in-up animation-delay-800">
        <div className="flex items-center gap-6 sm:gap-10">
          <span className="data-mono text-[11px] text-white/40 uppercase tracking-wider">
            6,197 boats
          </span>
          <span className="text-white/20">|</span>
          <span className="data-mono text-[11px] text-white/40 uppercase tracking-wider">
            31,000 results
          </span>
          <span className="text-white/20">|</span>
          <span className="data-mono text-[11px] text-white/40 uppercase tracking-wider">
            IRC analysis since 2024
          </span>
        </div>
      </div>

      {/* Bottom fade to sand */}
      <div
        className="absolute bottom-0 left-0 right-0 h-32 z-20"
        style={{
          background:
            "linear-gradient(to bottom, transparent 0%, #F7F3ED 100%)",
        }}
      />
    </section>
  );
}

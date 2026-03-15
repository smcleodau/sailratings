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

      {/* Warm gradient overlay */}
      <div
        className="absolute inset-0 z-10"
        style={{
          background:
            "linear-gradient(to bottom, rgba(42,31,26,0.40) 0%, rgba(42,31,26,0.12) 30%, rgba(42,31,26,0.08) 55%, rgba(42,31,26,0.35) 80%, rgba(42,31,26,0.60) 100%)",
        }}
      />

      {/* Content — push text up to leave room for search below */}
      <div className="relative z-20 flex flex-col items-center justify-start h-full px-6 text-center pt-[18vh] sm:pt-[16vh]">
        <h1
          className="heading-serif text-white text-4xl sm:text-5xl md:text-6xl lg:text-7xl max-w-4xl mb-6"
          style={{ textShadow: "0 4px 30px rgba(60,20,0,0.4), 0 1px 3px rgba(30,10,0,0.3)" }}
        >
          What is your rating <em className="italic">really</em> costing you?
        </h1>
        <p
          className="body-text text-white/80 text-lg sm:text-xl md:text-2xl max-w-2xl italic font-light mb-2"
          style={{ textShadow: "0 1px 10px rgba(60,20,0,0.25)" }}
        >
          We&rsquo;ve analysed 6,000+ IRC boats, 31,000 race results, and
          thousands of certificates to find where your points are hiding.
        </p>
      </div>

      {/* Bottom fade to linen */}
      <div
        className="absolute bottom-0 left-0 right-0 h-32 z-20"
        style={{
          background:
            "linear-gradient(to bottom, transparent 0%, #FAF6F0 100%)",
        }}
      />
    </section>
  );
}

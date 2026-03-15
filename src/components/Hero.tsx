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

      {/* Navy gradient overlay */}
      <div
        className="absolute inset-0 z-10"
        style={{
          background:
            "linear-gradient(to bottom, rgba(15,27,45,0.45) 0%, rgba(15,27,45,0.2) 35%, rgba(15,27,45,0.3) 65%, rgba(15,27,45,0.7) 100%)",
        }}
      />

      {/* Content */}
      <div className="relative z-20 flex flex-col items-center justify-center h-full px-6 text-center">
        <h1
          className="heading-serif text-white text-4xl sm:text-5xl md:text-6xl lg:text-7xl max-w-4xl mb-6"
          style={{ textShadow: "0 2px 20px rgba(0,0,0,0.3)" }}
        >
          What is your rating really costing you?
        </h1>
        <p
          className="body-text text-white/85 text-lg sm:text-xl md:text-2xl max-w-2xl italic font-light"
          style={{ textShadow: "0 1px 10px rgba(0,0,0,0.3)" }}
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

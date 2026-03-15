import Hero from "@/components/Hero";
import PageFlow from "@/components/PageFlow";

export default function Home() {
  return (
    <main className="min-h-screen bg-sand">
      <Hero />
      <PageFlow />

      {/* Footer */}
      <footer className="border-t border-border-light px-6 py-10">
        <div className="max-w-2xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-muted">
          <span className="brand-wordmark text-xs text-muted/60">Sail Ratings</span>
          <span className="body-text text-center">
            IRC rating data sourced from public certificates. Not affiliated
            with the RORC Rating Office.
          </span>
        </div>
      </footer>
    </main>
  );
}

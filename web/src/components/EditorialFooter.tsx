export default function EditorialFooter() {
  return (
    <footer className="border-t border-border-light px-6 py-10 bg-cream">
      <div className="max-w-4xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-[11px] text-muted">
        <span className="brand-wordmark text-muted/40">Sail Ratings</span>
        <span className="body-text text-center">
          Rating data sourced from public certificates. Not affiliated with the
          RORC Rating Office or ORC.
        </span>
      </div>
    </footer>
  );
}

const BARS = [0.4, 0.65, 0.5, 0.85, 0.6, 0.72];

function MiniBars({ className = "" }: { className?: string }) {
  return (
    <div className={`flex items-end gap-1.5 ${className}`}>
      {BARS.map((h, i) => (
        <div
          key={i}
          className="flex-1 rounded-t-sm"
          style={{
            height: `${h * 100}%`,
            background:
              i % 3 === 0
                ? "linear-gradient(180deg, #ddc38c, #b8985b)"
                : "rgba(11,12,14,0.14)",
          }}
        />
      ))}
    </div>
  );
}

/** "Paper" mockup for the branded PDF report. */
export function PdfMockup() {
  return (
    <div className="flex h-full flex-col rounded-lg bg-white p-5 shadow-[0_20px_50px_-20px_rgba(11,12,14,0.35)]">
      <div className="flex items-center justify-between border-b border-black/10 pb-3">
        <div className="flex items-center gap-2">
          <div className="h-3 w-3 rounded-full bg-gold" />
          <div className="h-2 w-20 rounded-full bg-black/15" />
        </div>
        <div className="h-2 w-10 rounded-full bg-black/10" />
      </div>
      <div className="mt-4 h-3 w-2/3 rounded-full bg-black/20" />
      <div className="mt-2 h-2 w-1/2 rounded-full bg-black/10" />
      <div className="mt-5 h-24 flex-none">
        <MiniBars />
      </div>
      <div className="mt-5 grid grid-cols-3 gap-2">
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-md bg-black/[0.04] p-2">
            <div className="h-1.5 w-8 rounded-full bg-black/15" />
            <div className="mt-1.5 h-2.5 w-10 rounded-full bg-black/25" />
          </div>
        ))}
      </div>
      <div className="mt-4 flex-1 space-y-1.5">
        <div className="h-1.5 w-full rounded-full bg-black/[0.08]" />
        <div className="h-1.5 w-5/6 rounded-full bg-black/[0.08]" />
        <div className="h-1.5 w-2/3 rounded-full bg-black/[0.08]" />
      </div>
    </div>
  );
}

/** Browser-chrome mockup for the interactive HTML dashboard. */
export function DashboardMockup() {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg bg-mkt-ink shadow-[0_20px_50px_-20px_rgba(11,12,14,0.5)]">
      <div className="flex items-center gap-1.5 border-b border-white/10 px-3 py-2.5">
        <span className="h-2 w-2 rounded-full bg-white/20" />
        <span className="h-2 w-2 rounded-full bg-white/20" />
        <span className="h-2 w-2 rounded-full bg-white/20" />
        <div className="ml-2 h-2 w-24 rounded-full bg-white/10" />
      </div>
      <div className="grid flex-1 grid-cols-3 gap-2 p-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-md border border-white/10 bg-white/[0.03] p-2">
            <div className="h-1.5 w-8 rounded-full bg-white/20" />
            <div className="mt-2 h-2.5 w-12 rounded-full bg-gold-light/80" />
          </div>
        ))}
        <div className="col-span-3 mt-1 h-20 rounded-md border border-white/10 bg-white/[0.03] p-2.5">
          <MiniBars />
        </div>
      </div>
    </div>
  );
}

/** Tile-grid mockup for the Power BI export. */
export function PowerBiMockup() {
  return (
    <div className="flex h-full flex-col rounded-lg bg-canvas-soft p-4 shadow-[0_20px_50px_-20px_rgba(11,12,14,0.35)]">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="h-2.5 w-2.5 rounded-sm bg-gold" />
          <div className="h-2 w-16 rounded-full bg-black/15" />
        </div>
        <div className="h-2 w-8 rounded-full bg-black/10" />
      </div>
      <div className="mt-4 grid flex-1 grid-cols-2 gap-2.5">
        <div className="rounded-md bg-white p-2.5 shadow-sm">
          <div className="h-1.5 w-10 rounded-full bg-black/15" />
          <div className="mt-2 h-12">
            <MiniBars />
          </div>
        </div>
        <div className="flex flex-col gap-2.5">
          <div className="flex-1 rounded-md bg-white p-2.5 shadow-sm">
            <div className="h-1.5 w-8 rounded-full bg-black/15" />
            <div className="mt-1.5 h-2.5 w-12 rounded-full bg-gold-dark/70" />
          </div>
          <div className="flex-1 rounded-md bg-white p-2.5 shadow-sm">
            <div className="h-1.5 w-8 rounded-full bg-black/15" />
            <div className="mt-1.5 h-2.5 w-10 rounded-full bg-black/25" />
          </div>
        </div>
      </div>
    </div>
  );
}

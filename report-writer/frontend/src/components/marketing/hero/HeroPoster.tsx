/**
 * Static stand-in for the 3D data-landscape: used for prefers-reduced-motion,
 * low-power/mobile devices, and as the Suspense/loading fallback while the
 * WebGL bundle streams in. Echoes the same visual language (bar terrain +
 * verify-blue glow) so the experience never feels like a placeholder box.
 */
export function HeroPoster({ animated = false }: { animated?: boolean }) {
  const bars = [38, 58, 44, 80, 52, 70, 46, 92, 58, 40, 74, 50, 64, 36];

  return (
    <div className="relative h-full w-full overflow-hidden bg-mkt-ink">
      <div
        className={`absolute inset-0 ${animated ? "animate-pulse" : ""}`}
        style={{
          background:
            "radial-gradient(55% 45% at 50% 38%, rgba(30,79,209,0.28), transparent 70%)",
          animationDuration: "7s",
        }}
      />
      <div className="absolute inset-x-0 bottom-0 flex h-[42%] items-end justify-center gap-[2.5%] px-[10%] pb-[12%] opacity-80">
        {bars.map((h, i) => (
          <div
            key={i}
            className="w-full rounded-t-[2px]"
            style={{
              height: `${h}%`,
              background:
                i % 3 === 0
                  ? "linear-gradient(180deg, var(--color-verify-light), rgba(30,79,209,0.15))"
                  : "linear-gradient(180deg, rgba(242,241,234,0.28), rgba(242,241,234,0.03))",
            }}
          />
        ))}
      </div>
    </div>
  );
}

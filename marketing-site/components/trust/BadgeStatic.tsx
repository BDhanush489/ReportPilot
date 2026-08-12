/** Static, already-assembled seal -- used for reduced-motion and low-power
 * devices, and as the loading fallback while the WebGL bundle streams in. */
export function BadgeStatic() {
  const ticks = Array.from({ length: 32 }, (_, i) => i);

  return (
    <svg
      viewBox="0 0 240 240"
      className="h-full w-full max-h-[360px] max-w-[360px]"
      role="img"
      aria-label="ReportPilot verified badge: a locked gold seal representing a machine-verified report"
    >
      <defs>
        <radialGradient id="seal-glow" cx="50%" cy="42%" r="65%">
          <stop offset="0%" stopColor="#f2dfb2" />
          <stop offset="55%" stopColor="#b8985b" />
          <stop offset="100%" stopColor="#8a6b3b" />
        </radialGradient>
      </defs>

      {ticks.map((i) => {
        const angle = (i / ticks.length) * Math.PI * 2;
        const x1 = 120 + Math.cos(angle) * 108;
        const y1 = 120 + Math.sin(angle) * 108;
        const x2 = 120 + Math.cos(angle) * 118;
        const y2 = 120 + Math.sin(angle) * 118;
        return (
          <line
            key={i}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke="#ddc38c"
            strokeWidth={2}
            strokeOpacity={0.55}
          />
        );
      })}

      <circle cx="120" cy="120" r="96" fill="url(#seal-glow)" />
      <circle
        cx="120"
        cy="120"
        r="96"
        fill="none"
        stroke="#0b0c0e"
        strokeOpacity={0.25}
        strokeWidth={1.5}
      />
      <circle
        cx="120"
        cy="120"
        r="78"
        fill="none"
        stroke="#0b0c0e"
        strokeOpacity={0.18}
        strokeWidth={1}
      />

      <path
        d="M92 122 L113 143 L150 100"
        fill="none"
        stroke="#0b0c0e"
        strokeWidth={9}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

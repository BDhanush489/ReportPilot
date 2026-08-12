"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { usePrefersReducedMotion } from "@/lib/hooks/usePrefersReducedMotion";
import { useLowPowerDevice } from "@/lib/hooks/useLowPowerDevice";
import { BadgeStatic } from "./BadgeStatic";

const BadgeCanvas = dynamic(
  () => import("./BadgeCanvas").then((m) => m.BadgeCanvas),
  { ssr: false, loading: () => <BadgeStatic /> },
);

export function BadgeMoment() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [armed, setArmed] = useState(false);
  const reducedMotion = usePrefersReducedMotion();
  const lowPower = useLowPowerDevice();
  const useStatic = reducedMotion || lowPower;

  useEffect(() => {
    if (useStatic) return;
    const el = containerRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setArmed(true);
          observer.disconnect();
        }
      },
      { threshold: 0.35 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [useStatic]);

  return (
    <div
      ref={containerRef}
      aria-hidden="true"
      className="relative mx-auto aspect-square w-full max-w-105"
    >
      <div
        aria-hidden
        className="absolute inset-0 rounded-full"
        style={{
          background:
            "radial-gradient(50% 50% at 50% 50%, rgba(184,152,91,0.25), transparent 70%)",
        }}
      />
      {useStatic ? (
        <BadgeStatic />
      ) : (
        <Suspense fallback={<BadgeStatic />}>
          <BadgeCanvas armed={armed} />
        </Suspense>
      )}
    </div>
  );
}

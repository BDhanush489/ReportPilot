"use client";

import { Suspense } from "react";
import dynamic from "next/dynamic";
import { usePrefersReducedMotion } from "@/lib/marketing/hooks/usePrefersReducedMotion";
import { useLowPowerDevice } from "@/lib/marketing/hooks/useLowPowerDevice";
import { HeroPoster } from "./HeroPoster";

const DataLandscapeCanvas = dynamic(
  () => import("./DataLandscapeCanvas").then((m) => m.DataLandscapeCanvas),
  { ssr: false, loading: () => <HeroPoster animated={false} /> },
);

export function HeroScene() {
  const reducedMotion = usePrefersReducedMotion();
  const lowPower = useLowPowerDevice();
  const useStatic = reducedMotion || lowPower;

  return (
    <div className="absolute inset-0" aria-hidden="true">
      {useStatic ? (
        <HeroPoster animated={!reducedMotion} />
      ) : (
        <Suspense fallback={<HeroPoster animated={false} />}>
          <DataLandscapeCanvas />
        </Suspense>
      )}
    </div>
  );
}

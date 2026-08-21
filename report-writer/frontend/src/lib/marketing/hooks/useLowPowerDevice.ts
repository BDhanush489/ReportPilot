"use client";

import { useSyncExternalStore } from "react";

const WIDTH_QUERY = "(max-width: 767px)";
const POINTER_QUERY = "(pointer: coarse)";

function subscribe(callback: () => void) {
  const widthMql = window.matchMedia(WIDTH_QUERY);
  const pointerMql = window.matchMedia(POINTER_QUERY);
  widthMql.addEventListener("change", callback);
  pointerMql.addEventListener("change", callback);
  return () => {
    widthMql.removeEventListener("change", callback);
    pointerMql.removeEventListener("change", callback);
  };
}

function getSnapshot() {
  return window.matchMedia(WIDTH_QUERY).matches || window.matchMedia(POINTER_QUERY).matches;
}

function getServerSnapshot() {
  // Default TRUE (not false) -- React uses this value for the very first
  // client render too (to match SSR output before hydration reconciles
  // against the real matchMedia result). Defaulting false meant every
  // device, phones included, briefly rendered <DataLandscapeCanvas/> on
  // that first pass -- which is enough for its dynamic import() (three.js
  // + @react-three/fiber + drei, a heavy bundle) to have already fired
  // over the network before the correction to the static poster landed a
  // moment later. Defaulting true means only a device that POSITIVELY
  // confirms it isn't low-power ever triggers that import at all; desktop
  // pays a brief "poster, then upgrade" flash instead, which is the right
  // trade since phones never pay for the heavy bundle now.
  return true;
}

/**
 * True for small screens / coarse-pointer (touch) devices, where the full
 * 3D mesh is skipped in favor of the static poster — 3D on the hero must
 * never jank a phone.
 */
export function useLowPowerDevice(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

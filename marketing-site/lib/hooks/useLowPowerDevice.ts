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
  return false;
}

/**
 * True for small screens / coarse-pointer (touch) devices, where the full
 * 3D mesh is skipped in favor of the static poster — 3D on the hero must
 * never jank a phone.
 */
export function useLowPowerDevice(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

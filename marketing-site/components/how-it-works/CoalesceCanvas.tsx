"use client";

import { useEffect, useRef } from "react";
import { useScroll } from "framer-motion";
import { usePrefersReducedMotion } from "@/lib/hooks/usePrefersReducedMotion";

const BAR_HEIGHTS = [0.32, 0.5, 0.4, 0.68, 0.56, 0.82, 0.62];
const PARTICLES_PER_BAR = 11;

function mulberry32(seed: number) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Particle = {
  startX: number;
  startY: number;
  endX: number;
  endY: number;
  radius: number;
  gold: boolean;
};

function buildParticles(rand: () => number): Particle[] {
  const particles: Particle[] = [];
  const barWidth = 1 / (BAR_HEIGHTS.length * 1.7);
  const gap = barWidth * 0.7;
  const totalWidth = BAR_HEIGHTS.length * barWidth + (BAR_HEIGHTS.length - 1) * gap;
  const startX0 = (1 - totalWidth) / 2;

  BAR_HEIGHTS.forEach((h, barIndex) => {
    const barX = startX0 + barIndex * (barWidth + gap);
    for (let i = 0; i < PARTICLES_PER_BAR; i++) {
      const endX = barX + rand() * barWidth;
      const endY = 0.82 - rand() * h * 0.82;
      particles.push({
        startX: rand(),
        startY: rand() * 0.75,
        endX,
        endY,
        radius: 1.4 + rand() * 1.6,
        gold: barIndex % 3 === 0 || rand() > 0.72,
      });
    }
  });
  return particles;
}

function easeInOutCubic(t: number) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

export function CoalesceCanvas() {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const reducedMotion = usePrefersReducedMotion();
  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ["start 0.85", "start 0.25"],
  });

  useEffect(() => {
    particlesRef.current = buildParticles(mulberry32(1337));
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let width = 0;
    let height = 0;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const resize = () => {
      const rect = container.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);

    let raf = 0;
    let visible = false;
    const draw = () => {
      if (!visible) return;
      const progress = reducedMotion ? 1 : easeInOutCubic(scrollYProgress.get());
      ctx.clearRect(0, 0, width, height);
      for (const p of particlesRef.current) {
        const x = (p.startX + (p.endX - p.startX) * progress) * width;
        const y = (p.startY + (p.endY - p.startY) * progress) * height;
        const alpha = 0.35 + progress * 0.65;
        ctx.beginPath();
        ctx.arc(x, y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = p.gold
          ? `rgba(221,195,140,${alpha})`
          : `rgba(247,244,236,${alpha * 0.6})`;
        ctx.fill();
      }
      raf = requestAnimationFrame(draw);
    };

    const intersectionObserver = new IntersectionObserver(
      ([entry]) => {
        visible = entry.isIntersecting;
        if (visible) {
          cancelAnimationFrame(raf);
          raf = requestAnimationFrame(draw);
        }
      },
      { threshold: 0 },
    );
    intersectionObserver.observe(container);

    return () => {
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
    };
  }, [reducedMotion, scrollYProgress]);

  return (
    <div ref={containerRef} className="relative h-70 w-full md:h-85">
      <canvas ref={canvasRef} className="absolute inset-0" aria-hidden="true" />
    </div>
  );
}

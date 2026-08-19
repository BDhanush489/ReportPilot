"use client";

import { Suspense, useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

const RIDGE_COUNT = 32;

function easeOutCubic(t: number) {
  return 1 - Math.pow(1 - t, 3);
}
function easeOutBack(t: number) {
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
}
function phase(t: number, start: number, end: number) {
  return THREE.MathUtils.clamp((t - start) / (end - start), 0, 1);
}

function Ridges({ t }: { t: React.RefObject<number> }) {
  const groupRef = useRef<THREE.Group>(null);
  const items = useMemo(
    () =>
      Array.from({ length: RIDGE_COUNT }, (_, i) => {
        const angle = (i / RIDGE_COUNT) * Math.PI * 2;
        return {
          angle,
          final: new THREE.Vector3(Math.cos(angle) * 1.18, Math.sin(angle) * 1.18, 0.02),
          start: new THREE.Vector3(
            Math.cos(angle) * 2.7,
            Math.sin(angle) * 2.7,
            i % 2 === 0 ? 0.5 : -0.5,
          ),
        };
      }),
    [],
  );

  useFrame(() => {
    const g = groupRef.current;
    if (!g) return;
    const p = easeOutCubic(phase(t.current ?? 0, 0, 0.55));
    g.children.forEach((child, i) => {
      const mesh = child as THREE.Mesh;
      const { start, final } = items[i];
      mesh.position.lerpVectors(start, final, p);
      const mat = mesh.material as THREE.MeshStandardMaterial;
      mat.opacity = 0.35 + p * 0.65;
    });
  });

  return (
    <group ref={groupRef}>
      {items.map((item, i) => (
        <mesh key={i} rotation={[0, 0, item.angle]}>
          <boxGeometry args={[0.1, 0.045, 0.045]} />
          <meshStandardMaterial
            color="#4d74e8"
            metalness={0.6}
            roughness={0.3}
            transparent
            opacity={0.35}
          />
        </mesh>
      ))}
    </group>
  );
}

function Rings({ t }: { t: React.RefObject<number> }) {
  const outer = useRef<THREE.Mesh>(null);
  const inner = useRef<THREE.Mesh>(null);

  useFrame(() => {
    const pOuter = easeOutCubic(phase(t.current ?? 0, 0.1, 0.6));
    const pInner = easeOutCubic(phase(t.current ?? 0, 0.2, 0.68));
    if (outer.current) {
      outer.current.rotation.z = THREE.MathUtils.lerp(-Math.PI * 0.6, 0, pOuter);
      const mat = outer.current.material as THREE.MeshStandardMaterial;
      mat.opacity = pOuter;
      outer.current.scale.setScalar(0.85 + pOuter * 0.15);
    }
    if (inner.current) {
      inner.current.rotation.z = THREE.MathUtils.lerp(Math.PI * 0.5, 0, pInner);
      const mat = inner.current.material as THREE.MeshStandardMaterial;
      mat.opacity = pInner;
      inner.current.scale.setScalar(0.85 + pInner * 0.15);
    }
  });

  return (
    <>
      <mesh ref={outer} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[1.3, 0.05, 16, 64]} />
        <meshStandardMaterial color="#4d74e8" metalness={0.7} roughness={0.25} transparent opacity={0} />
      </mesh>
      <mesh ref={inner} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[1.02, 0.035, 16, 64]} />
        <meshStandardMaterial color="#a8bdf5" metalness={0.5} roughness={0.3} transparent opacity={0} />
      </mesh>
    </>
  );
}

function Seal({ t }: { t: React.RefObject<number> }) {
  const disc = useRef<THREE.Mesh>(null);
  const checkA = useRef<THREE.Mesh>(null);
  const checkB = useRef<THREE.Mesh>(null);

  useFrame(() => {
    const pDisc = easeOutBack(phase(t.current ?? 0, 0.4, 0.8));
    const pCheck = easeOutBack(phase(t.current ?? 0, 0.72, 1));
    if (disc.current) disc.current.scale.setScalar(Math.max(pDisc, 0));
    if (checkA.current) checkA.current.scale.setScalar(Math.max(pCheck, 0));
    if (checkB.current) checkB.current.scale.setScalar(Math.max(pCheck, 0));
  });

  return (
    <group>
      <mesh ref={disc} rotation={[Math.PI / 2, 0, 0]} scale={0}>
        <cylinderGeometry args={[0.92, 0.92, 0.1, 48]} />
        <meshStandardMaterial
          color="#2b58d6"
          metalness={0.55}
          roughness={0.32}
          emissive="#17399e"
          emissiveIntensity={0.25}
        />
      </mesh>
      <mesh ref={checkA} position={[-0.16, -0.02, 0.09]} rotation={[0, 0, Math.PI / 4]} scale={0}>
        <boxGeometry args={[0.42, 0.1, 0.05]} />
        <meshStandardMaterial color="#f2f1ea" roughness={0.4} />
      </mesh>
      <mesh ref={checkB} position={[0.14, 0.12, 0.09]} rotation={[0, 0, -Math.PI / 4]} scale={0}>
        <boxGeometry args={[0.62, 0.1, 0.05]} />
        <meshStandardMaterial color="#f2f1ea" roughness={0.4} />
      </mesh>
    </group>
  );
}

function Assembly({ armed }: { armed: boolean }) {
  const groupRef = useRef<THREE.Group>(null);
  const t = useRef(0);
  const startTime = useRef<number | null>(null);
  const DURATION = 2.1;

  useFrame((state, delta) => {
    if (armed && startTime.current === null) {
      startTime.current = state.clock.elapsedTime;
    }
    if (startTime.current !== null) {
      t.current = THREE.MathUtils.clamp(
        (state.clock.elapsedTime - startTime.current) / DURATION,
        0,
        1,
      );
    }
    if (groupRef.current && t.current >= 1) {
      groupRef.current.rotation.y += delta * 0.06;
    }
  });

  return (
    <group ref={groupRef}>
      <Ridges t={t} />
      <Rings t={t} />
      <Seal t={t} />
    </group>
  );
}

export function BadgeCanvas({ armed }: { armed: boolean }) {
  return (
    <Canvas dpr={[1, 1.75]} camera={{ position: [0, 0, 4.4], fov: 34 }} gl={{ antialias: true, alpha: true }}>
      <ambientLight intensity={0.65} color="#8f97a8" />
      <directionalLight position={[3, 4, 5]} intensity={1.2} color="#eef1f8" />
      <pointLight position={[-3, -1, 2]} intensity={0.6} color="#1e4fd1" />
      <Suspense fallback={null}>
        <Assembly armed={armed} />
      </Suspense>
    </Canvas>
  );
}

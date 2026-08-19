"use client";

import { Suspense, useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows } from "@react-three/drei";
import * as THREE from "three";

const GRID_X = 14;
const GRID_Z = 9;
const SPACING = 0.6;
const BAR_FOOTPRINT = 0.44;
const MIN_HEIGHT = 0.35;
const MAX_HEIGHT = 2.6;

/** Deterministic, hand-tuned wave field -- not random noise, so the "terrain"
 * reads as intentional data topology rather than static. */
function heightAt(xi: number, zi: number) {
  const nx = xi / GRID_X;
  const nz = zi / GRID_Z;
  const wave =
    Math.sin(nx * Math.PI * 2.1 + nz * 1.3) * 0.5 +
    Math.sin(nz * Math.PI * 1.7 - nx * 0.6) * 0.35 +
    Math.sin((nx + nz) * Math.PI * 3.1) * 0.15;
  const t = (wave + 1) / 2; // 0..1
  return MIN_HEIGHT + t * (MAX_HEIGHT - MIN_HEIGHT);
}

function Terrain() {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const count = GRID_X * GRID_Z;
  const dummy = useMemo(() => new THREE.Object3D(), []);

  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    const colorLow = new THREE.Color("#22242b");
    const colorHigh = new THREE.Color("#4d74e8");
    let i = 0;
    for (let xi = 0; xi < GRID_X; xi++) {
      for (let zi = 0; zi < GRID_Z; zi++) {
        const h = heightAt(xi, zi);
        dummy.position.set(
          (xi - (GRID_X - 1) / 2) * SPACING,
          h / 2 - 0.5,
          (zi - (GRID_Z - 1) / 2) * SPACING,
        );
        dummy.scale.set(1, h, 1);
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
        const t = THREE.MathUtils.clamp(
          (h - MIN_HEIGHT) / (MAX_HEIGHT - MIN_HEIGHT),
          0,
          1,
        );
        mesh.setColorAt(i, colorLow.clone().lerp(colorHigh, t));
        i++;
      }
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [dummy]);

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, count]} receiveShadow>
      <boxGeometry args={[BAR_FOOTPRINT, 1, BAR_FOOTPRINT]} />
      <meshStandardMaterial roughness={0.55} metalness={0.25} />
    </instancedMesh>
  );
}

function GlassPanel({
  position,
  rotation,
}: {
  position: [number, number, number];
  rotation: [number, number, number];
}) {
  const bars = [0.28, 0.52, 0.38, 0.72, 0.46];
  return (
    <group position={position} rotation={rotation}>
      <mesh>
        <planeGeometry args={[1.7, 1.05]} />
        <meshPhysicalMaterial
          color="#f2f1ea"
          transparent
          opacity={0.07}
          roughness={0.2}
          metalness={0}
          side={THREE.DoubleSide}
        />
      </mesh>
      <mesh>
        <planeGeometry args={[1.7, 1.05]} />
        <meshBasicMaterial color="#4d74e8" wireframe transparent opacity={0.22} />
      </mesh>
      {bars.map((h, i) => (
        <mesh key={i} position={[-0.62 + i * 0.31, -0.42 + h / 2, 0.02]}>
          <boxGeometry args={[0.17, h, 0.02]} />
          <meshStandardMaterial
            color="#4d74e8"
            emissive="#17399e"
            emissiveIntensity={0.35}
            roughness={0.3}
          />
        </mesh>
      ))}
    </group>
  );
}

function Rig({ children }: { children: React.ReactNode }) {
  const group = useRef<THREE.Group>(null);

  useFrame((state, delta) => {
    const g = group.current;
    if (!g) return;
    g.rotation.y += delta * 0.045;
    const targetX = state.pointer.y * 0.1;
    const targetZ = -state.pointer.x * 0.12;
    g.rotation.x = THREE.MathUtils.damp(g.rotation.x, targetX, 4, delta);
    g.rotation.z = THREE.MathUtils.damp(g.rotation.z, targetZ, 4, delta);
  });

  return <group ref={group}>{children}</group>;
}

export function DataLandscapeCanvas() {
  return (
    <Canvas
      dpr={[1, 1.75]}
      camera={{ position: [0, 3.3, 7.4], fov: 32 }}
      gl={{ antialias: true, alpha: true }}
    >
      <color attach="background" args={["#12151a"]} />
      <fog attach="fog" args={["#12151a", 8, 15]} />
      <ambientLight intensity={0.6} color="#8f97a8" />
      <directionalLight position={[4, 6, 3]} intensity={1.15} color="#eef1f8" />
      <pointLight position={[-4, 2.2, -3]} intensity={0.9} color="#1e4fd1" />
      <Suspense fallback={null}>
        <Rig>
          <Terrain />
          <GlassPanel position={[-2.7, 2.15, -1]} rotation={[-0.15, 0.35, 0.05]} />
          <GlassPanel position={[2.5, 2.6, -1.7]} rotation={[-0.1, -0.32, -0.04]} />
        </Rig>
        <ContactShadows
          position={[0, -0.5, 0]}
          opacity={0.55}
          scale={13}
          blur={2.4}
          far={4}
          color="#000000"
        />
      </Suspense>
    </Canvas>
  );
}

"use client";

import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { useRef } from "react";
import * as THREE from "three";

function RotatingCore() {
  const meshRef = useRef<THREE.Mesh>(null!);

  useFrame(() => {
    meshRef.current.rotation.y += 0.01;
    meshRef.current.rotation.x += 0.005;
  });

  return (
    <mesh ref={meshRef}>
      <sphereGeometry args={[1.5, 64, 64]} />
      <meshStandardMaterial
        color="#00d9ff"
        emissive="#00d9ff"
        emissiveIntensity={2}
        wireframe
      />
    </mesh>
  );
}

export default function Jarvis3D() {
  return (
    <div className="h-[500px] w-full">
      <Canvas camera={{ position: [0, 0, 5] }}>
        <ambientLight intensity={1.5} />
        <pointLight position={[10, 10, 10]} intensity={3} />
        <RotatingCore />
        <OrbitControls enableZoom={false} />
      </Canvas>
    </div>
  );
}
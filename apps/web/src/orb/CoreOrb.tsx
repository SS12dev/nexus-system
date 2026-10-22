import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import { AdditiveBlending, BackSide, Color } from "three";
import type { AssistantState } from "../lib/types";
import { ORB_R } from "./model";

/** Look of each state: base colour, how much the surface churns and how fast. */
const LOOK: Record<AssistantState, { color: string; amp: number; speed: number; pulse: number }> = {
  idle: { color: "#38bdf8", amp: 0.025, speed: 0.25, pulse: 0 },
  thinking: { color: "#a78bfa", amp: 0.07, speed: 0.9, pulse: 0.02 },
  speaking: { color: "#67e8f9", amp: 0.05, speed: 0.6, pulse: 0.05 },
};

// Ashima/Stefan Gustavson 3D simplex noise (MIT).
const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.0-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;vec4 s1=floor(b1)*2.0+1.0;vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

const vertexShader = /* glsl */ `
uniform float uTime;
uniform float uAmp;
varying vec3 vNormal;
varying vec3 vView;
varying float vNoise;
${NOISE}
void main(){
  float n = snoise(normal * 1.3 + vec3(uTime));
  vNoise = n;
  vec3 pos = position + normal * n * uAmp;
  vec4 mv = modelViewMatrix * vec4(pos, 1.0);
  vNormal = normalize(normalMatrix * normal);
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;

// Mostly see-through in the middle so memories and artifacts inside stay visible; the rim
// glows (fresnel) and the noise adds slow bright veins.
const fragmentShader = /* glsl */ `
uniform vec3 uColor;
uniform float uGlow;
varying vec3 vNormal;
varying vec3 vView;
varying float vNoise;
void main(){
  float fres = pow(1.0 - max(dot(vNormal, vView), 0.0), 2.4);
  float veins = smoothstep(0.35, 0.9, vNoise) * 0.35;
  vec3 col = uColor * (0.35 + fres * 1.6 * uGlow) + vec3(veins) * uColor;
  float alpha = clamp(0.06 + fres * 0.85 + veins, 0.0, 1.0);
  gl_FragColor = vec4(col, alpha);
}`;

export function CoreOrb({ state, still }: { state: AssistantState; still: boolean }) {
  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uAmp: { value: LOOK.idle.amp },
      uColor: { value: new Color(LOOK.idle.color) },
      uGlow: { value: 1 },
    }),
    [],
  );
  const haloUniforms = useMemo(
    () => ({ uColor: { value: new Color(LOOK.idle.color) }, uGlow: { value: 1 } }),
    [],
  );
  const target = useMemo(() => new Color(), []);
  const clock = useRef(0);

  useFrame((_, dt) => {
    const look = LOOK[state] ?? LOOK.idle;
    const k = still ? 1 : 1 - Math.exp(-dt * 4);
    target.set(look.color);
    uniforms.uColor.value.lerp(target, k);
    haloUniforms.uColor.value.copy(uniforms.uColor.value);
    uniforms.uAmp.value += (look.amp - uniforms.uAmp.value) * k;
    if (!still) clock.current += dt * look.speed;
    uniforms.uTime.value = clock.current;
    const beat = still ? 0 : Math.sin(performance.now() / 160) * look.pulse;
    uniforms.uGlow.value = 1 + beat * 6;
    haloUniforms.uGlow.value = 1 + beat * 8;
  });

  return (
    <group>
      {/* Raycasts pass through the shell so taps reach the memories and crystals inside. */}
      <mesh raycast={() => null} renderOrder={2}>
        <icosahedronGeometry args={[ORB_R, 48]} />
        <shaderMaterial
          uniforms={uniforms}
          vertexShader={vertexShader}
          fragmentShader={fragmentShader}
          transparent
          depthWrite={false}
        />
      </mesh>
      <mesh raycast={() => null} scale={1.28}>
        <sphereGeometry args={[ORB_R, 32, 32]} />
        <shaderMaterial
          uniforms={haloUniforms}
          side={BackSide}
          blending={AdditiveBlending}
          transparent
          depthWrite={false}
          vertexShader={/* glsl */ `
            varying vec3 vNormal; varying vec3 vView;
            void main(){
              vec4 mv = modelViewMatrix * vec4(position, 1.0);
              vNormal = normalize(normalMatrix * normal); vView = normalize(-mv.xyz);
              gl_Position = projectionMatrix * mv;
            }`}
          fragmentShader={/* glsl */ `
            uniform vec3 uColor; uniform float uGlow;
            varying vec3 vNormal; varying vec3 vView;
            void main(){
              float i = pow(max(-dot(vNormal, vView), 0.0), 2.0) * 0.45 * uGlow;
              gl_FragColor = vec4(uColor * i, i);
            }`}
        />
      </mesh>
    </group>
  );
}

// GLSL sources for the background scene (ShaderMaterial, GLSL ES 3.0 via three).
// Every fragment shader runs its colour through `grade()` so the error tint and
// brightness dips apply uniformly in both render paths (composer and direct).

/** 3D simplex noise. Ashima Arts / Stefan Gustavson, MIT licence. */
const NOISE = /* glsl */ `
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 10.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.5 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 105.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}
`;

/** Shared grade: error tint (desaturated red) and global gain. */
const GRADE = /* glsl */ `
uniform float uError;
uniform float uGain;
vec3 grade(vec3 c) {
  float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
  vec3 red = vec3(1.0, 0.13, 0.16) * (l * 1.5 + 0.0015);
  return mix(c, red, uError * 0.78) * uGain;
}
// Soft exponential shoulder for opaque surfaces (HDR in, <= 1 out).
vec3 finish(vec3 c) { return vec3(1.0) - exp(-grade(c) * 1.1); }
`;

// ---------------------------------------------------------------- orbs

export const ORB_VERT = /* glsl */ `
uniform float uTime;
uniform float uAmp;
uniform float uFreq;
uniform float uSpeed;
uniform float uSeed;
varying vec3 vNormalW;
varying vec3 vPosW;
varying vec3 vDir;
varying float vNoise;
${NOISE}
float field(vec3 p) {
  vec3 q = p * uFreq + vec3(uSeed);
  float t = uTime * uSpeed;
  float n = snoise(q + vec3(0.0, t, t * 0.7));
  n += 0.5 * snoise(q * 2.03 + vec3(t * 1.3, -t, 1.7));
  return n / 1.5;
}
vec3 displaced(vec3 dir, out float n) {
  n = field(dir);
  return dir * (1.0 + n * uAmp);
}
void main() {
  vec3 dir = normalize(position);
  vec3 ref = abs(dir.y) < 0.99 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
  vec3 t = normalize(cross(dir, ref));
  vec3 b = cross(dir, t);
  float n0; float n1; float n2;
  vec3 p0 = displaced(dir, n0);
  vec3 p1 = displaced(normalize(dir + t * 0.02), n1);
  vec3 p2 = displaced(normalize(dir + b * 0.02), n2);
  vec3 nrm = normalize(cross(p1 - p0, p2 - p0));
  vNoise = n0;
  vDir = dir;
  vec4 world = modelMatrix * vec4(p0, 1.0);
  vPosW = world.xyz;
  vNormalW = normalize(mat3(modelMatrix) * nrm);
  gl_Position = projectionMatrix * viewMatrix * world;
}
`;

export const ORB_FRAG = /* glsl */ `
uniform float uTime;
uniform vec3 uDeep;
uniform vec3 uBase;
uniform vec3 uGlow;
uniform float uIntensity;
varying vec3 vNormalW;
varying vec3 vPosW;
varying vec3 vDir;
varying float vNoise;
${NOISE}
${GRADE}
void main() {
  vec3 N = normalize(vNormalW);
  vec3 V = normalize(cameraPosition - vPosW);
  float ndv = clamp(dot(N, V), 0.0, 1.0);
  float rim = pow(1.0 - ndv, 2.6);
  float center = pow(ndv, 1.3);
  float t = uTime;
  // Domain-warped flow: soft large-scale variation plus thin luminous filaments.
  vec3 q = vDir * 1.35;
  float n1 = snoise(q + vec3(0.0, t * 0.05, t * 0.04));
  float n2 = snoise(q * 2.2 + vec3(t * 0.06, -t * 0.045, 0.0) + n1 * 0.6 + vNoise * 0.4);
  float fil = pow(1.0 - abs(n2), 10.0);
  // Glassy body: deep centre, saturated limb, luminous rim.
  vec3 body = mix(uBase, uDeep, center * 0.72) * (0.82 + 0.28 * n1);
  vec3 col = body * (0.42 + 0.48 * uIntensity);
  col += uGlow * fil * (0.1 + 0.5 * uIntensity) * (0.3 + 0.7 * center);
  col += uGlow * rim * (0.4 + 1.2 * uIntensity);
  vec3 L = normalize(vec3(-0.45, 0.62, 0.64));
  col += uGlow * pow(max(dot(N, normalize(L + V)), 0.0), 48.0) * 0.22;
  gl_FragColor = vec4(finish(col), 1.0);
  #include <colorspace_fragment>
}
`;

// ---------------------------------------------------------------- halos (camera-facing glow cards)

export const HALO_VERT = /* glsl */ `
uniform vec3 uCenter;
uniform float uSize;
varying vec2 vUv;
void main() {
  vUv = uv * 2.0 - 1.0;
  vec4 mv = viewMatrix * vec4(uCenter, 1.0);
  mv.xy += position.xy * uSize;
  gl_Position = projectionMatrix * mv;
}
`;

export const HALO_FRAG = /* glsl */ `
uniform vec3 uColor;
uniform float uStrength;
uniform float uFalloff;
varying vec2 vUv;
${GRADE}
void main() {
  float r2 = dot(vUv, vUv);
  // Tight glow hugging the orb plus a faint wide skirt.
  float a = exp(-r2 * uFalloff) * 0.88 + exp(-r2 * uFalloff * 0.3) * 0.12;
  a *= 1.0 - smoothstep(0.55, 1.0, r2);
  gl_FragColor = vec4(grade(uColor) * a * uStrength, 1.0);
  #include <colorspace_fragment>
}
`;

// ---------------------------------------------------------------- core (iridescent crystal)

export const CORE_VERT = /* glsl */ `
varying vec3 vNormalW;
varying vec3 vPosW;
varying vec3 vDir;
void main() {
  vDir = normalize(position);
  vec4 world = modelMatrix * vec4(position, 1.0);
  vPosW = world.xyz;
  vNormalW = normalize(mat3(modelMatrix) * normal);
  gl_Position = projectionMatrix * viewMatrix * world;
}
`;

export const CORE_FRAG = /* glsl */ `
uniform float uTime;
uniform float uIntensity;
varying vec3 vNormalW;
varying vec3 vPosW;
varying vec3 vDir;
${NOISE}
${GRADE}
void main() {
  vec3 N = normalize(vNormalW);
  vec3 V = normalize(cameraPosition - vPosW);
  float ndv = clamp(dot(N, V), 0.0, 1.0);
  float f = 1.0 - ndv;
  float n = snoise(vDir * 1.8 + vec3(0.0, uTime * 0.07, uTime * 0.05));
  // Thin-film iridescence (soap bubble / pearl): hue follows the view angle.
  vec3 film = 0.5 + 0.5 * cos(6.28318 * (vec3(0.0, 0.33, 0.67) + f * 1.5 + n * 0.18 + uTime * 0.03));
  vec3 pearl = vec3(0.86, 0.88, 1.0);
  vec3 col = mix(pearl * 0.3, film, smoothstep(0.05, 0.85, f)) * (0.3 + 1.5 * pow(f, 1.7));
  col += pearl * pow(ndv, 5.0) * 0.45;
  gl_FragColor = vec4(finish(col * uIntensity), 1.0);
  #include <colorspace_fragment>
}
`;

// ---------------------------------------------------------------- ambient particle field

export const FIELD_VERT = /* glsl */ `
uniform float uTime;
uniform float uPointScale;
uniform float uPixelRatio;
uniform vec3 uTintA;
uniform vec3 uTintB;
uniform vec3 uStar;
attribute vec3 aSeed; // x: phase, y: size, z: tint selector
varying float vAlpha;
varying vec3 vTint;
void main() {
  vec3 p = position;
  float ph = aSeed.x * 6.28318;
  p += vec3(sin(uTime * 0.05 + ph), cos(uTime * 0.043 + ph * 1.3), sin(uTime * 0.037 + ph * 0.7)) * 0.22;
  float a = uTime * 0.006;
  p.xz = mat2(cos(a), -sin(a), sin(a), cos(a)) * p.xz;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  float twinkle = 0.6 + 0.4 * sin(uTime * (0.5 + aSeed.y * 1.4) + ph * 3.0);
  float size = (0.02 + aSeed.y * 0.06) * uPointScale / max(-mv.z, 0.1);
  // Keep sub-pixel points visible without shimmering: clamp size, fade alpha.
  float minSize = 1.4 * uPixelRatio;
  vAlpha = twinkle * (0.22 + 0.55 * aSeed.y) * clamp(size / minSize, 0.0, 1.0);
  gl_PointSize = clamp(size, minSize, 9.0 * uPixelRatio);
  vTint = aSeed.z < 0.14 ? uTintA : (aSeed.z > 0.86 ? uTintB : uStar);
}
`;

export const POINT_FRAG = /* glsl */ `
varying float vAlpha;
varying vec3 vTint;
${GRADE}
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  if (d > 0.5) discard;
  float a = smoothstep(0.5, 0.0, d);
  a *= a;
  gl_FragColor = vec4(grade(vTint) * a * vAlpha, 1.0);
  #include <colorspace_fragment>
}
`;

// ---------------------------------------------------------------- debate stream

export const STREAM_VERT = /* glsl */ `
uniform float uTime;
uniform float uStream;
uniform float uSynth;
uniform float uFlow;
uniform float uOrbRadius;
uniform float uPointScale;
uniform float uPixelRatio;
uniform vec3 uA;
uniform vec3 uB;
uniform vec3 uCore;
uniform vec3 uColA;
uniform vec3 uColB;
attribute vec4 aSeed; // x: progress offset, y: direction (+1 A->B, -1 B->A), z: speed, w: size
attribute vec2 aLane; // offset inside the tube (unit disc)
varying float vAlpha;
varying vec3 vTint;
vec3 bez(vec3 a, vec3 b, vec3 c, vec3 d, float t) {
  float u = 1.0 - t;
  return u * u * u * a + 3.0 * u * u * t * b + 3.0 * u * t * t * c + t * t * t * d;
}
vec3 bezd(vec3 a, vec3 b, vec3 c, vec3 d, float t) {
  float u = 1.0 - t;
  return 3.0 * u * u * (b - a) + 6.0 * u * t * (c - b) + 3.0 * t * t * (d - c);
}
void main() {
  if (uStream < 0.002) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
    gl_PointSize = 0.0;
    vAlpha = 0.0;
    vTint = vec3(0.0);
    return;
  }
  float dir = aSeed.y;
  bool fwd = dir > 0.0;
  vec3 src = fwd ? uA : uB;
  vec3 other = fwd ? uB : uA;
  vec3 dst = mix(other, uCore, uSynth);
  vec3 axis = dst - src;
  float len = max(length(axis), 1e-3);
  vec3 ax = axis / len;
  float t = fract(aSeed.x + uTime * uFlow * aSeed.z);
  // Leave from and arrive at the orb surfaces; into the core during synthesis.
  vec3 p0 = src + ax * uOrbRadius * 0.9;
  vec3 p3 = dst - ax * mix(uOrbRadius * 0.9, 0.18, uSynth);
  // A->B arcs over the top, B->A underneath (and slightly in front).
  float spread = 0.72 + 0.56 * fract(aSeed.x * 7.31 + aSeed.w * 13.7);
  vec3 lift = normalize(vec3(0.0, 1.0, 0.35)) * dir * len * mix(0.3, 0.14, uSynth) * spread;
  vec3 p1 = mix(p0, p3, 0.3) + lift;
  vec3 p2 = mix(p0, p3, 0.7) + lift;
  vec3 pos = bez(p0, p1, p2, p3, t);
  vec3 T = normalize(bezd(p0, p1, p2, p3, t));
  vec3 N1 = normalize(cross(T, vec3(0.0, 0.0, 1.0)));
  vec3 N2 = cross(T, N1);
  float ang = uTime * 1.2 * dir + t * 5.0;
  vec2 lane = mat2(cos(ang), -sin(ang), sin(ang), cos(ang)) * aLane;
  float tube = 0.08 + 0.3 * pow(abs(2.0 * t - 1.0), 2.4);
  pos += (N1 * lane.x + N2 * lane.y) * tube;
  vec4 mv = modelViewMatrix * vec4(pos, 1.0);
  gl_Position = projectionMatrix * mv;
  float ends = pow(sin(3.14159 * t), 0.7);
  float size = aSeed.w * uPointScale / max(-mv.z, 0.1);
  float minSize = 1.5 * uPixelRatio;
  vAlpha = uStream * ends * (0.35 + 0.65 * aSeed.z) * clamp(size / minSize, 0.0, 1.0);
  gl_PointSize = clamp(size, minSize, 10.0 * uPixelRatio);
  vTint = fwd ? uColA : uColB;
}
`;

// ---------------------------------------------------------------- consensus shock ring

export const RING_VERT = /* glsl */ `
varying vec2 vP;
void main() {
  vP = position.xy;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

export const RING_FRAG = /* glsl */ `
uniform float uRadius;
uniform float uWidth;
uniform float uAlpha;
uniform vec3 uColA;
uniform vec3 uColB;
varying vec2 vP;
${GRADE}
void main() {
  float r = length(vP);
  float ring = exp(-pow((r - uRadius) / uWidth, 2.0));
  float wake = exp(-pow((r - uRadius * 0.86) / (uWidth * 2.5), 2.0)) * 0.1;
  float side = 0.5 + 0.5 * (vP.x / max(r, 1e-3));
  vec3 col = mix(mix(uColA, uColB, side), vec3(1.0), 0.3);
  gl_FragColor = vec4(grade(col) * (ring + wake) * uAlpha, 1.0);
  #include <colorspace_fragment>
}
`;

// ---------------------------------------------------------------- background

export const BG_VERT = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = position.xy * 0.5 + 0.5;
  gl_Position = vec4(position.xy, 1.0, 1.0);
}
`;

export const BG_FRAG = /* glsl */ `
uniform float uTime;
uniform float uAspect;
uniform vec2 uFocus;
uniform vec3 uBg;
uniform vec3 uTintA;
uniform vec3 uTintB;
varying vec2 vUv;
${NOISE}
${GRADE}
void main() {
  // Distances in units of the shorter screen side (portrait stays dark too).
  vec2 p = (vUv - uFocus) * vec2(uAspect, 1.0) / min(uAspect, 1.0);
  float r = length(p);
  float n = snoise(vec3(p * 1.1, uTime * 0.012)) * 0.5 + 0.5;
  float m = snoise(vec3(p * 2.6 + 11.0, uTime * 0.018)) * 0.5 + 0.5;
  float neb = smoothstep(0.3, 1.0, n * 0.75 + m * 0.45);
  vec3 tint = mix(uTintA, uTintB, smoothstep(-0.55, 0.55, p.x));
  vec3 col = uBg;
  col += tint * neb * 0.016 * exp(-r * r * 1.4);
  col += vec3(0.0022, 0.0028, 0.0065) * exp(-r * r * 2.4);
  col *= 1.0 - 0.3 * smoothstep(0.6, 1.7, r);
  col = grade(col);
  // Dither in (approximate) sRGB so the dark gradients never band.
  float h = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453);
  vec3 s = pow(max(col, 0.0), vec3(1.0 / 2.2)) + (h - 0.5) / 255.0;
  col = pow(max(s, 0.0), vec3(2.2));
  gl_FragColor = vec4(col, 1.0);
  #include <colorspace_fragment>
}
`;

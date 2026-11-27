// "Now playing" disc: the artwork clipped to a wavy outline, with the song's
// progress drawn along a matching wavy ring and a dot marking the position.
// Music mode is a wavy circle; TV mode is a wavy 16:9 rectangle for the video.
// The ring is the seek control:
//   touch      drag the dot (or anywhere on the ring) around the shape
//   controller with the disc selected, D-pad left/right = -/+ 10 seconds

import { Field, GamepadButton } from "@decky/ui";
import { useMemo, useRef, useState, PointerEvent as ReactPointerEvent, ReactNode, CSSProperties } from "react";

const corner: CSSProperties = {
  position: "absolute", fontSize: 13, letterSpacing: 0.5, opacity: 0.85,
  fontVariantNumeric: "tabular-nums", pointerEvents: "none",
};

// A wavy closed outline: a rounded rectangle (straight edges joined by true
// quarter-circle corners; with no straight part it's a circle) with a wave
// running along it. Points are spaced evenly along it, so the waves are even
// and the progress dot moves at a steady speed.
type Outline = { points: [number, number][]; path: string; length: number };

function outline(cx: number, cy: number, a: number, b: number, radius: number, waves: number, depth: number): Outline {
  const sx = a - radius, sy = b - radius;   // half lengths of the straight parts
  const arc = (Math.PI / 2) * radius;
  type Piece = [number, (t: number) => [number, number, number, number]];  // length, t -> x, y, normal
  const line = (x0: number, y0: number, x1: number, y1: number, nx: number, ny: number): Piece[1] =>
    t => [x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, nx, ny];
  const corner = (ox: number, oy: number, start: number): Piece[1] => t => {
    const angle = start + (t * Math.PI) / 2;
    return [ox + radius * Math.cos(angle), oy + radius * Math.sin(angle), Math.cos(angle), Math.sin(angle)];
  };
  // Clockwise from the top centre.
  const pieces = ([
    [sx, line(0, -b, sx, -b, 0, -1)],
    [arc, corner(sx, -sy, -Math.PI / 2)],
    [2 * sy, line(a, -sy, a, sy, 1, 0)],
    [arc, corner(sx, sy, 0)],
    [2 * sx, line(sx, b, -sx, b, 0, 1)],
    [arc, corner(-sx, sy, Math.PI / 2)],
    [2 * sy, line(-a, sy, -a, -sy, -1, 0)],
    [arc, corner(-sx, -sy, Math.PI)],
    [sx, line(-sx, -b, 0, -b, 0, -1)],
  ] as Piece[]).filter(([length]) => length > 1e-9);
  const total = pieces.reduce((sum, [length]) => sum + length, 0);

  const count = 720;
  const points: [number, number][] = [];
  for (let k = 0; k < count; k++) {
    let s = (k / count) * total;
    let at = pieces[pieces.length - 1][1](1);
    for (const [length, f] of pieces) {
      if (s <= length) { at = f(s / length); break; }
      s -= length;
    }
    const [x, y, nx, ny] = at;
    const offset = depth * Math.cos((2 * Math.PI * waves * k) / count);
    points.push([cx + x + nx * offset, cy + y + ny * offset]);
  }
  let d = "", length = 0;
  points.forEach(([x, y], i) => {
    d += `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
    if (i) length += Math.hypot(x - points[i - 1][0], y - points[i - 1][1]);
  });
  length += Math.hypot(points[0][0] - points[count - 1][0], points[0][1] - points[count - 1][1]);
  return { points, path: `${d}Z`, length };
}

type Geometry = {
  width: number; height: number;        // drawing size
  display: number;                       // on-screen width
  art: Outline; ring: Outline;
  box: { x: number; y: number; w: number; h: number };  // artwork / video area
  inside: (x: number, y: number) => boolean;            // on the artwork (not the ring)
};

const GEOMETRY: Record<"circle" | "rect", () => Geometry> = {
  // Music: the wavy circle (10 waves).
  circle: () => {
    const size = 216, c = size / 2, art = 82, ring = 98;
    return {
      width: size, height: size, display: 180,
      art: outline(c, c, art, art, art, 10, art * 0.045),
      ring: outline(c, c, ring, ring, ring, 10, ring * 0.045),
      box: { x: c - art * 1.06, y: c - art * 1.06, w: art * 2.12, h: art * 2.12 },
      inside: (x, y) => Math.hypot(x - c, y - c) < art * 0.85,
    };
  },
  // TV: a wavy 16:9 rectangle with round corners, waves in step with the ring.
  rect: () => {
    const width = 300, height = 184, cx = width / 2, cy = height / 2;
    const aw = 124, ah = 70;   // half sizes of the video (16:9)
    return {
      width, height, display: 272,
      art: outline(cx, cy, aw, ah, 28, 16, 2.2),
      ring: outline(cx, cy, aw + 16, ah + 16, 40, 16, 3.0),
      box: { x: cx - aw - 3, y: cy - ah - 3, w: aw * 2 + 6, h: ah * 2 + 6 },
      inside: (x, y) => Math.abs(x - cx) < aw * 0.9 && Math.abs(y - cy) < ah * 0.85,
    };
  },
};

type Props = {
  art: string | null;
  position: number;
  duration: number;
  disabled?: boolean;
  onSeek: (seconds: number) => void;       // final position after a drag
  onSeekBy: (delta: number) => void;       // controller steps
  onDrag?: (seconds: number | null) => void; // live position while dragging
  onSecondary?: () => void;
  // Elapsed / total, shown beside the shape.
  cornerLeft?: ReactNode;
  cornerRight?: ReactNode;
  // TV mode: the wavy rectangle, showing `screen` (the pixel video) instead of the art.
  shape?: "circle" | "rect";
  screen?: ReactNode;
};

export function Disc({ art, position, duration, disabled, onSeek, onSeekBy, onDrag, onSecondary, cornerLeft, cornerRight,
  shape = "circle", screen }: Props) {
  const g = useMemo(() => GEOMETRY[shape](), [shape]);
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<number | null>(null); // 0..1 while dragging
  const clipId = useMemo(() => `cuivar-disc-${Math.random().toString(36).slice(2)}`, []);

  const ratio = drag ?? (duration > 0 ? Math.min(1, Math.max(0, position / duration)) : 0);
  const ringPoints = g.ring.points;
  const [dotX, dotY] = ringPoints[Math.min(ringPoints.length - 1, Math.floor(ratio * ringPoints.length))];

  // A touch -> drawing coordinates, and the nearest spot on the ring as 0..1.
  const locate = (event: ReactPointerEvent<SVGSVGElement>) => {
    const box = svgRef.current!.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * g.width;
    const y = ((event.clientY - box.top) / box.height) * g.height;
    let best = 0, bestDistance = Infinity;
    ringPoints.forEach(([px, py], i) => {
      const d = (px - x) ** 2 + (py - y) ** 2;
      if (d < bestDistance) { bestDistance = d; best = i; }
    });
    return { ratio: best / ringPoints.length, inside: g.inside(x, y) };
  };

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (disabled || duration <= 0) return;
    const { ratio: r, inside } = locate(event);
    // Only the ring area seeks; tapping the artwork itself does nothing.
    if (inside) return;
    svgRef.current!.setPointerCapture(event.pointerId);
    setDrag(r);
    onDrag?.(r * duration);
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (drag === null) return;
    // Don't let a drag wrap from the end straight back to the start.
    let r = locate(event).ratio;
    if (drag > 0.85 && r < 0.15) r = 0.999;
    if (drag < 0.15 && r > 0.85) r = 0;
    setDrag(r);
    onDrag?.(r * duration);
  };

  const finish = () => {
    if (drag === null) return;
    onSeek(Math.round(drag * duration));
    setDrag(null);
    onDrag?.(null);
  };

  // D-pad left/right on the selected disc seeks instead of moving focus.
  const onButtonDown = (event: CustomEvent<{ button: number }>) => {
    const button = event.detail?.button;
    if (button === GamepadButton.DIR_LEFT || button === GamepadButton.DIR_RIGHT) {
      event.preventDefault();
      event.stopPropagation();
      if (!disabled) onSeekBy(button === GamepadButton.DIR_LEFT ? -10 : 10);
    }
  };

  const extra = { onGamepadDirection: (event: CustomEvent<{ button: number }>) => {
    const button = event.detail?.button;
    return button === GamepadButton.DIR_LEFT || button === GamepadButton.DIR_RIGHT;
  } };

  const displayHeight = Math.round((g.display * g.height) / g.width);
  // The circle leaves empty top corners for the times; the rectangle is full
  // width, so the times go underneath it.
  const rect = shape === "rect";
  const { x, y, w, h } = g.box;

  // A focusable Field (not a plain Focusable, which is only a group the
  // D-pad skips) so the controller can select the disc.
  return (
    <Field
      focusable
      highlightOnFocus
      padding="none"
      bottomSeparator="none"
      onButtonDown={onButtonDown as any}
      onSecondaryButton={onSecondary}
      onSecondaryActionDescription="Song options"
      actionDescriptionMap={{ [GamepadButton.DIR_LEFT]: "−10 s", [GamepadButton.DIR_RIGHT]: "+10 s" } as any}
      {...(extra as any)}
      label={<div style={{
        display: "flex", justifyContent: "center", width: "100%", position: "relative",
        padding: rect ? "2px 0 18px" : "2px 0",
      }}>
      <div style={{ ...corner, left: 2, ...(rect ? { bottom: 0 } : { top: 4 }) }}>{cornerLeft}</div>
      <div style={{ ...corner, right: 2, textAlign: "right", ...(rect ? { bottom: 0 } : { top: 4 }) }}>{cornerRight}</div>
      <svg
        ref={svgRef}
        width={g.display}
        height={displayHeight}
        viewBox={`0 0 ${g.width} ${g.height}`}
        style={{ touchAction: "none", cursor: disabled ? "default" : "pointer" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={finish}
        onPointerCancel={finish}
      >
        <defs>
          <clipPath id={clipId}><path d={g.art.path} /></clipPath>
        </defs>
        <path d={g.art.path} fill="#1d1d1d" />
        {screen ? (
          <foreignObject x={x} y={y} width={w} height={h} clipPath={`url(#${clipId})`} style={{ pointerEvents: "none" }}>
            {screen}
          </foreignObject>
        ) : art && (
          <image href={art} x={x} y={y} width={w} height={h} preserveAspectRatio="xMidYMid slice" clipPath={`url(#${clipId})`} />
        )}
        {/* Track (remaining) and progress (played) along the same wave. */}
        <path d={g.ring.path} fill="none" stroke="rgba(255,255,255,0.22)" strokeWidth={2} />
        <path d={g.ring.path} fill="none" stroke="#ffffff" strokeWidth={3} strokeLinecap="round"
          strokeDasharray={`${(g.ring.length * ratio).toFixed(1)} ${g.ring.length.toFixed(1)}`} />
        <circle cx={dotX} cy={dotY} r={drag !== null ? 9 : 6.5} fill="#ffffff" />
        {/* Generous invisible ring so the finger doesn't need to hit the 3px line. */}
        <path d={g.ring.path} fill="none" stroke="transparent" strokeWidth={28} />
      </svg>
      </div>}
    />
  );
}

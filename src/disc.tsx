// "Now playing" disc: artwork clipped to a wavy (scalloped) shape, with the
// song's progress drawn as a ring along the same wave, and a dot marking the
// position. The ring is the seek control:
//   touch      drag the dot (or anywhere on the ring) around the circle
//   controller with the disc selected, D-pad left/right = -/+ 10 seconds

import { Field, GamepadButton } from "@decky/ui";
import { useMemo, useRef, useState, PointerEvent as ReactPointerEvent, ReactNode, CSSProperties } from "react";

const corner: CSSProperties = {
  position: "absolute", top: 4, fontSize: 13, letterSpacing: 0.5, opacity: 0.85,
  fontVariantNumeric: "tabular-nums", pointerEvents: "none",
};

const SIZE = 216;          // drawing size in px (fits the sidebar)
const C = SIZE / 2;         // centre
const ART_R = 82;           // artwork radius
const RING_R = 98;          // progress ring radius
const LOBES = 10;           // waves around the circle
const WAVE = 0.045;         // wave depth (fraction of the radius)
const POINTS = 360;

// Point on the wavy outline at angle t (0 = top, clockwise).
function wavePoint(radius: number, t: number) {
  const angle = t * 2 * Math.PI - Math.PI / 2;
  const r = radius * (1 + WAVE * Math.cos(LOBES * (angle + Math.PI / 2)));
  return [C + r * Math.cos(angle), C + r * Math.sin(angle)];
}

function wavePath(radius: number) {
  let d = "";
  for (let i = 0; i <= POINTS; i++) {
    const [x, y] = wavePoint(radius, i / POINTS);
    d += `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
  }
  return `${d}Z`;
}

function pathLength(radius: number) {
  let length = 0;
  let [px, py] = wavePoint(radius, 0);
  for (let i = 1; i <= POINTS; i++) {
    const [x, y] = wavePoint(radius, i / POINTS);
    length += Math.hypot(x - px, y - py);
    [px, py] = [x, y];
  }
  return length;
}

type Props = {
  art: string | null;
  position: number;
  duration: number;
  disabled?: boolean;
  onSeek: (seconds: number) => void;       // final position after a drag
  onSeekBy: (delta: number) => void;       // controller steps
  onDrag?: (seconds: number | null) => void; // live position while dragging
  onSecondary?: () => void;
  // Shown in the empty top corners beside the round disc (elapsed / total).
  cornerLeft?: ReactNode;
  cornerRight?: ReactNode;
};

// On-screen size; the drawing (SIZE) is scaled to fit so the whole player
// (disc, controls, buttons, volume) fits one Quick Access screen.
const DISPLAY = 180;

export function Disc({ art, position, duration, disabled, onSeek, onSeekBy, onDrag, onSecondary, cornerLeft, cornerRight }: Props) {
  const artPath = useMemo(() => wavePath(ART_R), []);
  const ringPath = useMemo(() => wavePath(RING_R), []);
  const ringLength = useMemo(() => pathLength(RING_R), []);
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<number | null>(null); // 0..1 while dragging
  const clipId = useMemo(() => `cuivar-disc-${Math.random().toString(36).slice(2)}`, []);

  const ratio = drag ?? (duration > 0 ? Math.min(1, Math.max(0, position / duration)) : 0);
  const [dotX, dotY] = wavePoint(RING_R, ratio);

  // Angle of a touch around the centre -> 0..1 (top = 0, clockwise).
  const ratioAt = (event: ReactPointerEvent<SVGSVGElement>) => {
    const box = svgRef.current!.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * SIZE - C;
    const y = ((event.clientY - box.top) / box.height) * SIZE - C;
    return { ratio: ((Math.atan2(y, x) + Math.PI / 2) / (2 * Math.PI) + 1) % 1, distance: Math.hypot(x, y) };
  };

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (disabled || duration <= 0) return;
    const { ratio: r, distance } = ratioAt(event);
    // Only the ring area seeks; tapping the artwork itself does nothing.
    if (distance < ART_R * 0.85) return;
    svgRef.current!.setPointerCapture(event.pointerId);
    setDrag(r);
    onDrag?.(r * duration);
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (drag === null) return;
    // Don't let a drag wrap from the end straight back to the start.
    let r = ratioAt(event).ratio;
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
      label={<div style={{ display: "flex", justifyContent: "center", width: "100%", padding: "2px 0", position: "relative" }}>
      <div style={{ ...corner, left: 2 }}>{cornerLeft}</div>
      <div style={{ ...corner, right: 2, textAlign: "right" }}>{cornerRight}</div>
      <svg
        ref={svgRef}
        width={DISPLAY}
        height={DISPLAY}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        style={{ touchAction: "none", cursor: disabled ? "default" : "pointer" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={finish}
        onPointerCancel={finish}
      >
        <defs>
          <clipPath id={clipId}><path d={artPath} /></clipPath>
        </defs>
        <path d={artPath} fill="#1d1d1d" />
        {art && (
          <image href={art} x={C - ART_R * 1.06} y={C - ART_R * 1.06} width={ART_R * 2.12} height={ART_R * 2.12}
            preserveAspectRatio="xMidYMid slice" clipPath={`url(#${clipId})`} />
        )}
        {/* Track (remaining) and progress (played) along the same wave. */}
        <path d={ringPath} fill="none" stroke="rgba(255,255,255,0.22)" strokeWidth={2} />
        <path d={ringPath} fill="none" stroke="#ffffff" strokeWidth={3} strokeLinecap="round"
          strokeDasharray={`${(ringLength * ratio).toFixed(1)} ${ringLength.toFixed(1)}`} />
        <circle cx={dotX} cy={dotY} r={drag !== null ? 9 : 6.5} fill="#ffffff" />
        {/* Generous invisible ring so the finger doesn't need to hit the 3px line. */}
        <path d={ringPath} fill="none" stroke="transparent" strokeWidth={28} />
      </svg>
      </div>}
    />
  );
}

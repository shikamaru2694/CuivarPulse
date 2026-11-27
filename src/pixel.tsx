// TV mode: the song's music video as pixel art. The backend finds and prepares
// a tiny version of the video (cuivar/video.py) only while TV mode is on
// screen. Here it plays hidden, and every frame is drawn onto a canvas in the
// chosen pixel style (palette, dither, scanlines) with hard square pixels: the
// browser's own video scaling would smooth it into a blurry low-res video.
// Also: the fullscreen page for both modes (Music = the artwork, TV = the video).

import { Focusable, GamepadButton, Navigation, Router } from "@decky/ui";
import { useEffect, useRef, useState, CSSProperties } from "react";
import { api, artistNames, PixelSize, PixelStatus, PixelStyle, PlayerState, useLibrary, usePlayerState } from "./api";

export const FULLSCREEN_ROUTE = "/cuivar-pulse-fullscreen";
// Pixel width: Medium suits the sidebar, Fine the full screen.
export const PANEL_SIZE: PixelSize = 64;
export const FULLSCREEN_SIZE: PixelSize = 96;

// A game in front: no pixel video at all (audio only, no extra work).
export const gameRunning = () => !!Router.MainRunningApp;

export function openFullscreen() {
  Navigation.Navigate(FULLSCREEN_ROUTE);
  Navigation.CloseSideMenus();
}

// --- fetching ------------------------------------------------------------------------

type Loaded = { id: string; size: number; url: string };

// While `active`, keeps the backend preparing the current song's pixel video
// (it stops a few seconds after we stop asking) and loads it at `size` once ready.
export function usePixelVideo(state: PlayerState | null, active: boolean, size: PixelSize) {
  const trackId = state?.track?.id ?? null;
  const [status, setStatus] = useState<PixelStatus["state"]>("idle");
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const loadedRef = useRef<Loaded | null>(null);
  loadedRef.current = loaded;

  useEffect(() => {
    if (!active || !trackId) {
      setStatus("idle");
      return;
    }
    let alive = true;
    const poll = async () => {
      if (gameRunning()) {
        setStatus("idle");
        return;
      }
      const next = await api.pixelWatch(trackId).catch(() => null);
      if (!alive || !next) return;
      setStatus(next.state);
      const have = loadedRef.current;
      if (next.state === "ready" && (have?.id !== trackId || have?.size !== size)) {
        const result = await api.pixelGet(trackId, size).catch(() => null);
        if (!alive || !result || !result.ok) return;
        const bytes = Uint8Array.from(atob(result.data), c => c.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bytes], { type: "video/webm" }));
        setLoaded(previous => {
          if (previous) URL.revokeObjectURL(previous.url);
          return { id: trackId, size: result.size, url };
        });
      }
    };
    poll();
    const timer = window.setInterval(poll, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [active, trackId, size]);

  useEffect(() => () => { if (loadedRef.current) URL.revokeObjectURL(loadedRef.current.url); }, []);

  const src = active && loaded && loaded.id === trackId ? loaded.url : null;
  const busy = active && (status === "finding" || status === "fetching" || status === "preparing");
  return { src, status, busy };
}

// --- pixel styles --------------------------------------------------------------------

// ENDESGA 32: a popular pixel-art palette (warm skin tones, rich darks).
const RETRO = [
  0xbe4a2f, 0xd77643, 0xead4aa, 0xe4a672, 0xb86f50, 0x733e39, 0x3e2731, 0xa22633, 0xe43b44, 0xf77622, 0xfeae34,
  0xfee761, 0x63c74d, 0x3e8948, 0x265c42, 0x193c3e, 0x124e89, 0x0099db, 0x2ce8f5, 0xffffff, 0xc0cbdc, 0x8b9bb4,
  0x5a6988, 0x3a4466, 0x262b44, 0x181425, 0xff0044, 0x68386c, 0xb55088, 0xf6757a, 0xe8b796, 0xc28569,
].map(c => [c >> 16, (c >> 8) & 255, c & 255]);
const GAMEBOY = [[15, 56, 15], [48, 98, 48], [139, 172, 15], [155, 188, 15]];
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map(v => v / 16 - 0.5);

const clamp = (v: number) => (v < 0 ? 0 : v > 255 ? 255 : v);
const retroCache = new Map<number, number>();
function nearestRetro(r: number, g: number, b: number) {
  const key = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3);
  let hit = retroCache.get(key);
  if (hit === undefined) {
    let best = Infinity;
    hit = 0;
    RETRO.forEach(([pr, pg, pb], i) => {
      const d = (pr - r) ** 2 * 0.3 + (pg - g) ** 2 * 0.59 + (pb - b) ** 2 * 0.11;
      if (d < best) { best = d; hit = i; }
    });
    retroCache.set(key, hit);
  }
  return RETRO[hit];
}

// Recolour the tiny frame in place.
function stylise(image: ImageData, style: PixelStyle) {
  const { data, width } = image;
  const punch = style === "gameboy" ? [1, 1.3] : style === "retro" ? [1.3, 1.1] : [1.4, 1.15];  // arcade: punchier
  const [saturation, contrast] = punch;
  for (let i = 0; i < data.length; i += 4) {
    const p = i / 4, x = p % width, y = (p - x) / width;
    let r = data[i], g = data[i + 1], b = data[i + 2];
    const luma = 0.299 * r + 0.587 * g + 0.114 * b;
    r = (luma + (r - luma) * saturation - 128) * contrast + 128;
    g = (luma + (g - luma) * saturation - 128) * contrast + 128;
    b = (luma + (b - luma) * saturation - 128) * contrast + 128;
    const t = BAYER[(y % 4) * 4 + (x % 4)];
    if (style === "retro") {
      [r, g, b] = nearestRetro(clamp(r + t * 22), clamp(g + t * 22), clamp(b + t * 22));
    } else if (style === "gameboy") {
      const level = clamp(0.299 * r + 0.587 * g + 0.114 * b + t * 64);
      [r, g, b] = GAMEBOY[Math.min(3, Math.floor(level / 64))];
    } else {
      // Arcade: the video's own colours, a few clean levels per channel.
      r = Math.round(clamp(r) / 42.5) * 42.5;
      g = Math.round(clamp(g) / 42.5) * 42.5;
      b = Math.round(clamp(b) / 42.5) * 42.5;
    }
    data[i] = clamp(r); data[i + 1] = clamp(g); data[i + 2] = clamp(b);
  }
}

// --- the screen ----------------------------------------------------------------------

// The pixel video (or, without one, the artwork) drawn in the chosen style,
// filling its box. Kept on the song's position minus the output delay (so on
// Bluetooth it matches what you hear); pause, seek and skip follow.
export function PixelScreen({ src, art, state, style, size, fit = "cover" }: {
  src: string | null; art: string | null; state: PlayerState; style: PixelStyle; size: number;
  fit?: "cover" | "contain";
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stamp = useRef({ position: 0, at: 0, playing: false, delay: 0 });
  useEffect(() => {
    stamp.current = { position: state.position, at: performance.now(), playing: state.playing, delay: state.outputDelay || 0 };
  }, [state.position, state.playing, state.outputDelay]);

  // Keep the hidden video on the song's position.
  useEffect(() => {
    if (!src) return;
    const timer = window.setInterval(() => {
      const video = videoRef.current;
      if (!video || !video.duration) return;
      const s = stamp.current;
      const expected = Math.max(0, s.position + (s.playing ? (performance.now() - s.at) / 1000 : 0) - s.delay);
      if (expected >= video.duration - 0.05) {
        if (!video.paused) video.pause();
        return;
      }
      if (Math.abs(video.currentTime - expected) > 0.25) video.currentTime = expected;
      if (s.playing && video.paused) video.play().catch(() => {});
      if (!s.playing && !video.paused) video.pause();
    }, 250);
    return () => window.clearInterval(timer);
  }, [src]);

  // Draw 12 times a second: the frame at pixel size, recoloured, then blown up
  // with hard edges onto the visible canvas.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const width = size, height = Math.round((size * 9) / 16);
    const tiny = document.createElement("canvas");
    tiny.width = width; tiny.height = height;
    const small = tiny.getContext("2d", { willReadFrequently: true })!;
    const image = new Image();
    image.crossOrigin = "anonymous";
    let artReady = false, drawnArt = false;
    image.onload = () => { artReady = true; drawnArt = false; };
    if (art) image.src = art;

    const draw = () => {
      const video = videoRef.current;
      const useVideo = !!src && !!video && video.readyState >= 2;
      if (!useVideo && (!artReady || drawnArt)) return;  // the artwork only needs drawing once
      const box = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      const cw = Math.max(1, Math.round(box.width * ratio)), ch = Math.max(1, Math.round(box.height * ratio));
      if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
      small.imageSmoothingEnabled = true;  // average down to pixel size...
      if (useVideo) {
        small.drawImage(video!, 0, 0, width, height);
      } else {
        // Square artwork into the 16:9 frame (centre crop).
        const side = image.naturalWidth, cropH = (side * 9) / 16;
        small.drawImage(image, 0, (image.naturalHeight - cropH) / 2, side, cropH, 0, 0, width, height);
        drawnArt = true;
      }
      try {
        const pixels = small.getImageData(0, 0, width, height);
        stylise(pixels, style);
        small.putImageData(pixels, 0, 0);
      } catch {
        // Artwork from a server that doesn't allow reading it: shown unstyled.
      }
      const big = canvas.getContext("2d")!;
      big.imageSmoothingEnabled = false;   // ...and up again with hard square edges
      big.fillStyle = "#000";
      big.fillRect(0, 0, cw, ch);
      const scale = fit === "contain" ? Math.min(cw / width, ch / height) : Math.max(cw / width, ch / height);
      const dw = width * scale, dh = height * scale;
      const ox = (cw - dw) / 2, oy = (ch - dh) / 2;
      big.drawImage(tiny, ox, oy, dw, dh);
      if (style === "arcade" && scale >= 3) {
        // CRT scanlines: a dark band along the bottom of every pixel row.
        big.fillStyle = "rgba(0,0,0,0.5)";
        for (let row = 0; row < height; row++) {
          big.fillRect(ox, oy + (row + 1) * scale - scale * 0.32, dw, scale * 0.32);
        }
      }
    };
    draw();
    const timer = window.setInterval(draw, 1000 / 12);
    return () => window.clearInterval(timer);
  }, [src, art, style, size, fit]);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", background: "#000" }}>
      {src && (
        // Hidden source; only its frames are used.
        <video ref={videoRef} src={src} muted playsInline preload="auto"
          style={{ position: "absolute", width: 1, height: 1, opacity: 0, pointerEvents: "none" }} />
      )}
      <canvas ref={canvasRef} style={{ display: "block", width: "100%", height: "100%" }} />
    </div>
  );
}

// --- fullscreen ----------------------------------------------------------------------

const full: Record<string, CSSProperties> = {
  screen: {
    position: "fixed", inset: 0, zIndex: 1000, background: "#000", display: "flex",
    alignItems: "center", justifyContent: "center", overflow: "hidden",
  },
  backdrop: {
    position: "absolute", inset: "-10%", backgroundSize: "cover", backgroundPosition: "center",
    filter: "blur(48px) brightness(0.45) saturate(1.3)",
  },
  art: {
    position: "relative", height: "72vh", aspectRatio: "1", objectFit: "cover", borderRadius: 14,
    boxShadow: "0 18px 60px rgba(0,0,0,0.6)",
  },
  tv: { position: "absolute", inset: 0 },
  caption: {
    position: "absolute", left: 0, right: 0, bottom: 20, textAlign: "center", fontSize: 16,
    color: "rgba(255,255,255,0.9)", textShadow: "0 1px 4px #000, 0 0 2px #000", letterSpacing: 0.3,
    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", padding: "0 40px",
  },
  hint: { position: "absolute", top: 14, right: 20, fontSize: 12, color: "rgba(255,255,255,0.45)" },
};

// Fullscreen page (the ⛶ button): Music mode = the artwork, TV mode = the pixel
// video. Any button closes it.
export function Fullscreen() {
  const state = usePlayerState();
  const [info] = useLibrary();
  const tvMode = !!state?.pixelVideo;
  const { src } = usePixelVideo(state, tvMode, FULLSCREEN_SIZE);
  const closed = useRef(false);
  const close = () => {
    if (closed.current) return;
    closed.current = true;
    Navigation.NavigateBack();
  };
  const track = state?.track;
  const art = track?.art ?? null;

  return (
    <Focusable
      style={full.screen}
      onActivate={close}
      onCancel={close}
      onButtonDown={((event: CustomEvent<{ button: number }>) => {
        if (event.detail?.button !== GamepadButton.CANCEL) close();  // B goes through onCancel
      }) as any}
      noFocusRing
      autoFocus
    >
      {tvMode && state ? (
        <div style={full.tv}>
          <PixelScreen src={src} art={art} state={state} fit="contain"
            style={info?.settings.pixelStyle ?? "retro"} size={FULLSCREEN_SIZE} />
        </div>
      ) : (
        <>
          {art && <div style={{ ...full.backdrop, backgroundImage: `url("${art}")` }} />}
          {art && <img src={art} style={full.art} />}
        </>
      )}
      <div style={full.caption}>
        {track ? `${track.title} · ${artistNames(track)}` : "Nothing playing. Start a song in Cuivar Pulse."}
      </div>
      <div style={full.hint}>Any button to close</div>
    </Focusable>
  );
}

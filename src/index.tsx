// Cuivar Pulse: Quick Access Menu panel.
// Shows what the background player is doing and sends it commands. The
// player keeps running when this panel closes or a game is in front.

import { definePlugin, addEventListener, removeEventListener, routerHook, useQuickAccessVisible } from "@decky/api";
import {
  PanelSection, PanelSectionRow, SliderField, TextField, Focusable, DialogButton, ButtonItem, Field,
  staticClasses, GamepadButton, ConfirmModal, showModal,
} from "@decky/ui";
import { useEffect, useRef, useState, CSSProperties } from "react";
import { FaMusic, FaDownload, FaCheck, FaListUl, FaPlus, FaCog } from "react-icons/fa";
import {
  MdRepeat, MdRepeatOne, MdShuffle, MdSkipPrevious, MdSkipNext, MdPlayArrow, MdPause, MdPlaylistAdd,
  MdDownload, MdDownloadDone, MdDownloading, MdVolumeUp, MdVolumeOff, MdMic, MdTv, MdMusicNote, MdFullscreen,
} from "react-icons/md";
import { Disc } from "./disc";
import { Fullscreen, FULLSCREEN_ROUTE, PANEL_SIZE, PixelScreen, gameRunning, openFullscreen, usePixelVideo } from "./pixel";
import { api, Track, PlayerState, LibraryInfo, artistNames, formatTime, formatBytes, usePlayerState, useLibrary } from "./api";
import {
  TrackLine, toast, openSongOptions, openAddToPlaylist, openNewPlaylist, openPlaylist, openDownloads,
} from "./modals";
import { openSettings } from "./settings";

const styles: Record<string, CSSProperties> = {
  nowPlaying: { display: "flex", gap: 12, alignItems: "center", width: "100%", paddingLeft: 8, boxSizing: "border-box" },
  nowCentered: { display: "flex", flexDirection: "column", alignItems: "center", gap: 8, width: "100%", textAlign: "center" },
  art: { width: 96, height: 96, borderRadius: 8, objectFit: "cover", background: "#1d1d1d", flexShrink: 0 },
  text: { minWidth: 0, display: "flex", flexDirection: "column", gap: 2 },
  centeredText: { minWidth: 0, maxWidth: "100%", display: "flex", flexDirection: "column", gap: 2, alignItems: "center" },
  subtitle: { fontSize: 12, opacity: 0.7, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  nowTitle: { fontWeight: 600, fontSize: 17, maxWidth: "100%", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  nowArtist: { fontSize: 14, opacity: 0.7, maxWidth: "100%", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  times: { display: "flex", justifyContent: "space-between", fontSize: 11, opacity: 0.7, width: "100%", paddingBottom: 4 },
  controls: { display: "flex", gap: 6, width: "100%", justifyContent: "space-between", alignItems: "center", padding: "4px 4px 0" },
  // Playlist | Music/TV switch (right under the play button) | Fullscreen, Download
  actionsRow: { display: "flex", alignItems: "center", width: "100%", marginTop: 4, padding: "0 4px", boxSizing: "border-box" },
  actionSide: { flex: 1, display: "flex", justifyContent: "space-evenly", alignItems: "center" },
  timeTop: { width: "100%", textAlign: "center", fontSize: 13, letterSpacing: 0.5, fontVariantNumeric: "tabular-nums", opacity: 0.85 },
  more: { fontSize: 12 },
  control: { minWidth: 0, flex: 1, height: 40, display: "flex", alignItems: "center", justifyContent: "center", padding: 0 },
  action: { minWidth: 0, flex: 1, height: 34, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, padding: 0, fontSize: 12 },
  note: { fontSize: 12, opacity: 0.7, width: "100%" },
  warn: { fontSize: 12, width: "100%", lineHeight: 1.4 },
  error: { fontSize: 12, color: "#ffb4a8", width: "100%" },
  libArt: { width: 40, height: 40, borderRadius: 4, objectFit: "cover", background: "#1d1d1d", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" },
  tabBar: { display: "flex", gap: 6, width: "100%", padding: 3, borderRadius: 10, background: "rgba(255,255,255,0.06)", boxSizing: "border-box" },
  tab: { minWidth: 0, width: "100%", padding: "6px 0", borderRadius: 8, fontSize: 14, fontWeight: 600, background: "transparent", color: "#fff", opacity: 0.7, boxShadow: "none" },
  tabActive: { background: "rgba(255,255,255,0.16)", opacity: 1 },
  tabFocused: { outline: "2px solid #1a9fff", outlineOffset: -2, opacity: 1, color: "#fff", background: "rgba(26,159,255,0.25)" },
  volumeRow: { display: "flex", alignItems: "center", gap: 10, width: "100%", marginTop: 6 },
  volumeIcon: { display: "flex", alignItems: "center", opacity: 0.8, flexShrink: 0 },
  titleBar: { display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", gap: 8 },
  voiceBanner: {
    position: "sticky", top: 0, zIndex: 5, display: "flex", alignItems: "center", gap: 10, padding: "10px 12px",
    margin: "0 16px 8px", borderRadius: 10, background: "rgba(16,40,64,0.96)", border: "1px solid rgba(26,159,255,0.6)",
    fontSize: 13, boxShadow: "0 4px 14px rgba(0,0,0,0.4)",
  },
  voiceMic: { display: "flex", color: "#1a9fff", animation: "cuivar-listen 1s ease-in-out infinite" },
  modeSwitch: {
    display: "flex", width: 92, height: 36, padding: 2, gap: 2, borderRadius: 18, boxSizing: "border-box", flexShrink: 0,
    background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,206,110,0.35)",
    boxShadow: "0 0 10px rgba(255,196,80,0.22), inset 0 0 6px rgba(255,196,80,0.08)",  // faint golden glow
  },
  modeBusy: { display: "flex", animation: "cuivar-listen 1s ease-in-out infinite" },
  engineSpin: { display: "inline-flex", verticalAlign: -2, marginRight: 6, animation: "cuivar-listen 1s ease-in-out infinite" },
  gear: { minWidth: 0, width: 40, height: 32, padding: 0, display: "flex", alignItems: "center", justifyContent: "center" },
};

// A song row: A (or tap) plays, X (or tap the cover) opens song options.
function TrackRow({ track, onPlay, downloaded }: { track: Track; onPlay: () => void; downloaded?: boolean }) {
  return (
    <PanelSectionRow>
      <Field
        focusable
        highlightOnFocus
        padding="compact"
        bottomSeparator="none"
        onActivate={onPlay}
        onClick={onPlay}
        onSecondaryButton={() => openSongOptions(track)}
        onSecondaryActionDescription="Song options"
        label={<TrackLine track={track} onArtClick={() => openSongOptions(track)} badge={downloaded ? <FaCheck /> : undefined} />}
      />
    </PanelSectionRow>
  );
}

// Round / pill buttons in the now-playing design. They have custom
// backgrounds, which hide Steam's usual focus colour, so they draw their own
// focus ring when the controller lands on them.
function DesignButton({ kind, active = true, selected = false, disabled, label, onClick, children }: {
  kind: "round" | "main" | "pill" | "icon" | "segment"; active?: boolean; selected?: boolean; disabled?: boolean; label: string;
  onClick: () => void; children: React.ReactNode;
}) {
  const [focused, setFocused] = useState(false);
  const base: CSSProperties = {
    minWidth: 0, padding: 0, display: "flex", alignItems: "center", justifyContent: "center",
    border: "none", transition: "box-shadow 120ms ease",
    boxShadow: focused ? "0 0 0 2px #ffffff, 0 0 0 5px rgba(26,159,255,0.9)" : "none",
  };
  const look: Record<string, CSSProperties> = {
    round: { width: 38, height: 38, borderRadius: 19, background: "transparent", color: "#ffffff", fontSize: 24, opacity: active ? 1 : 0.45 },
    main: { width: 54, height: 54, borderRadius: 27, background: "#ffffff", color: "#111111", fontSize: 30 },
    pill: {
      flex: 1, height: 36, borderRadius: 18, gap: 6, fontSize: 13, color: "#ffffff",
      background: selected ? "rgba(26,159,255,0.28)" : "rgba(255,255,255,0.06)",
      border: selected ? "1px solid #1a9fff" : "1px solid rgba(255,255,255,0.22)", opacity: disabled ? 0.6 : 1,
    },
    icon: {
      width: 40, height: 36, borderRadius: 18, color: "#ffffff", fontSize: 20,
      background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.22)", opacity: disabled ? 0.6 : 1,
    },
    segment: {
      flex: 1, height: "100%", borderRadius: 16, fontSize: 18, color: "#ffffff",
      background: selected ? "rgba(255,210,120,0.22)" : "transparent", opacity: selected ? 1 : 0.5,
    },
  };
  // Focus events bubble up to this wrapper (DialogButton doesn't take them).
  return (
    <div
      style={{ display: "flex", flex: kind === "pill" || kind === "segment" ? 1 : "0 0 auto" }}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
    >
      <DialogButton aria-label={label} style={{ ...base, ...look[kind] }} disabled={disabled} onClick={onClick}>
        {children}
      </DialogButton>
    </div>
  );
}

// A highlight-coloured arc that keeps circling the play button while a song loads.
function LoadingRing() {
  const size = 66, stroke = 3, r = (size - stroke) / 2, length = 2 * Math.PI * r;
  return (
    <>
      <style>{`@keyframes cuivar-spin { to { transform: rotate(360deg); } }`}</style>
      <svg
        width={size}
        height={size}
        style={{
          position: "absolute", left: "50%", top: "50%", margin: `-${size / 2}px 0 0 -${size / 2}px`,
          pointerEvents: "none", animation: "cuivar-spin 0.9s linear infinite",
        }}
      >
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(26,159,255,0.2)" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#1a9fff" strokeWidth={stroke}
          strokeLinecap="round" strokeDasharray={`${length * 0.28} ${length}`} />
      </svg>
    </>
  );
}

function NowPlaying({ state }: { state: PlayerState }) {
  // Position shown while dragging the ring, and after letting go until the
  // player confirms (so the dot doesn't jump back for a moment).
  const [dragSeconds, setDragSeconds] = useState<number | null>(null);
  const [pendingSeek, setPendingSeek] = useState<number | null>(null);
  // TV mode: pixel video only while the menu is actually open (no work in the background).
  const menuOpen = useQuickAccessVisible();
  const tvMode = state.pixelVideo;
  const pixel = usePixelVideo(state, tvMode && menuOpen, PANEL_SIZE);
  const [info] = useLibrary();
  useEffect(() => {
    if (pendingSeek !== null && Math.abs(state.position - pendingSeek) < 3) setPendingSeek(null);
  }, [state.position]);

  const track = state.track;
  if (!track) return null;

  const duration = Math.max(1, Math.round(state.duration || track.duration || 0));
  const position = dragSeconds ?? pendingSeek ?? Math.min(duration, state.position);
  const downloading = state.downloadingId === track.id || state.pendingIds.includes(track.id);
  const setMode = (tv: boolean) => {
    if (tv === tvMode) return;
    if (tv && gameRunning()) {
      toast("TV mode works when you're not in a game, to save battery.");
      return;
    }
    api.setPixelVideo(tv);
  };

  return (
    <>
      <PanelSectionRow>
        <Disc
          cornerLeft={formatTime(position)}
          cornerRight={formatTime(duration)}
          art={track.art}
          position={position}
          duration={duration}
          disabled={state.loading}
          onDrag={setDragSeconds}
          onSeek={seconds => { setPendingSeek(seconds); api.seek(seconds); }}
          onSeekBy={delta => api.seekBy(delta)}
          onSecondary={() => openSongOptions(track)}
          shape={tvMode ? "rect" : "circle"}
          screen={tvMode ? (
            <PixelScreen src={pixel.src} art={track.art} state={state}
              style={info?.settings.pixelStyle ?? "retro"} size={PANEL_SIZE} />
          ) : undefined}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={styles.nowCentered}>
          <div style={styles.centeredText}>
            <div style={styles.nowTitle}>{track.title}</div>
            <div style={styles.nowArtist}>{artistNames(track)}</div>
          </div>
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <Focusable style={styles.controls} flow-children="horizontal">
          <DesignButton kind="round" label="Repeat" active={state.repeat !== "off"} onClick={() => api.cycleRepeat()}>
            {state.repeat === "one" ? <MdRepeatOne /> : <MdRepeat />}
          </DesignButton>
          <DesignButton kind="round" label="Previous" onClick={() => api.previous()}><MdSkipPrevious /></DesignButton>
          <div style={{ position: "relative", display: "flex" }}>
            <DesignButton kind="main" label={state.playing ? "Pause" : "Play"} onClick={() => api.toggle()}>
              {state.playing ? <MdPause /> : <MdPlayArrow />}
            </DesignButton>
            {state.loading && <LoadingRing />}
          </div>
          <DesignButton kind="round" label="Next" onClick={() => api.next()}><MdSkipNext /></DesignButton>
          <DesignButton kind="round" label="Shuffle" active={state.shuffle} onClick={() => api.setShuffle(!state.shuffle)}>
            <MdShuffle />
          </DesignButton>
        </Focusable>
      </PanelSectionRow>
      <PanelSectionRow>
        <Focusable style={styles.actionsRow} flow-children="horizontal">
          <div style={styles.actionSide}>
            <DesignButton kind="round" label="Add to playlist" onClick={() => openAddToPlaylist(track)}>
              <MdPlaylistAdd />
            </DesignButton>
          </div>
          <Focusable style={styles.modeSwitch} flow-children="horizontal">
            <DesignButton kind="segment" label="Music mode" selected={!tvMode} onClick={() => setMode(false)}>
              <MdMusicNote />
            </DesignButton>
            <DesignButton kind="segment" label="TV mode" selected={tvMode} onClick={() => setMode(true)}>
              <span style={pixel.busy ? styles.modeBusy : { display: "flex" }}><MdTv /></span>
            </DesignButton>
          </Focusable>
          <div style={styles.actionSide}>
            <DesignButton kind="round" label="Fullscreen" onClick={() => {
              if (tvMode && gameRunning()) toast("TV mode works when you're not in a game, to save battery.");
              else openFullscreen();
            }}>
              <MdFullscreen />
            </DesignButton>
            <DesignButton
              kind="round"
              label={state.downloaded ? "Downloaded" : downloading ? "Downloading" : "Download"}
              active={!downloading}
              disabled={state.downloaded || downloading}
              onClick={() => api.downloadTracks([track]).then(r => r.ok && toast("Downloading for offline…"))}
            >
              {state.downloaded ? <MdDownloadDone /> : downloading ? <MdDownloading /> : <MdDownload />}
            </DesignButton>
          </div>
        </Focusable>
      </PanelSectionRow>
      {state.error && <PanelSectionRow><div style={styles.error}>{state.error}</div></PanelSectionRow>}
    </>
  );
}

function Search({ spoken }: { spoken: { text: string; n: number } | null }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Track[] | null>(null);
  const [status, setStatus] = useState("");

  const run = async (text = query) => {
    if (!text.trim()) return;
    setStatus("Searching…");
    const result = await api.search(text).catch(() => ({ ok: false as const, error: "Search failed." }));
    if (result.ok) {
      setResults(result.results);
      setStatus(result.results.length ? "" : "Nothing found.");
    } else {
      setStatus(result.error);
    }
  };

  // Voice search: show what was heard in the box (so it can be fixed with
  // the keyboard) and search for it straight away.
  useEffect(() => {
    if (!spoken) return;
    setQuery(spoken.text);
    run(spoken.text);
  }, [spoken?.n]);

  return (
    <PanelSection title="Search">
      <PanelSectionRow>
        <TextField
          placeholder="Songs, artists…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") run(); }}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => run()}>Search</ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={styles.note}><MdMic size={12} style={{ verticalAlign: -1 }} /> Hold Y to search by voice</div>
      </PanelSectionRow>
      {status && <PanelSectionRow><div style={styles.note}>{status}</div></PanelSectionRow>}
      {results && (
        <ExpandableList
          tracks={results}
          keyPrefix="s"
          resetKey={results.map(t => t.id).join()}
          onPlay={track => api.playTrack(track)}
        />
      )}
    </PanelSection>
  );
}

// Shows 6 songs, with "Show more" expanding to 12.
function ExpandableList({ tracks, keyPrefix, resetKey, onPlay }: {
  tracks: Track[]; keyPrefix: string; resetKey: string; onPlay: (track: Track, index: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => setExpanded(false), [resetKey]);
  const shown = tracks.slice(0, expanded ? 12 : 6);
  return (
    <>
      {shown.map((track, i) => (
        <TrackRow key={`${keyPrefix}-${track.id}-${i}`} track={track} onPlay={() => onPlay(track, i)} />
      ))}
      {tracks.length > 6 && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setExpanded(!expanded)}>
            <span style={styles.more}>{expanded ? "Show less" : "Show more"}</span>
          </ButtonItem>
        </PanelSectionRow>
      )}
    </>
  );
}

function Library({ info, state }: { info: LibraryInfo | null; state: PlayerState | null }) {
  const pending = state ? state.pendingIds.length + (state.downloadingId ? 1 : 0) : 0;
  return (
    <PanelSection title="Library">
      {pending > 0 && (
        <PanelSectionRow>
          <Field label={`Downloading… ${pending} left`} bottomSeparator="none" padding="compact">
            <DialogButton style={{ ...styles.action, minWidth: 80 }} onClick={() => api.cancelDownloads()}>Stop</DialogButton>
          </Field>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <Field focusable highlightOnFocus padding="compact" bottomSeparator="none" onActivate={openDownloads} onClick={openDownloads}
          label={
            <div style={styles.nowPlaying}>
              <div style={styles.libArt}><FaDownload /></div>
              <div style={styles.text}>
                <div style={{ fontWeight: 600, fontSize: 13 }}>Downloads</div>
                <div style={styles.subtitle}>{info ? `${info.usage.songs} songs · ${formatBytes(info.usage.bytes)}` : "…"}</div>
              </div>
            </div>
          } />
      </PanelSectionRow>
      {info?.playlists.map(p => (
        <PanelSectionRow key={p.id}>
          <Field focusable highlightOnFocus padding="compact" bottomSeparator="none"
            onActivate={() => openPlaylist(p.id)} onClick={() => openPlaylist(p.id)}
            label={
              <div style={styles.nowPlaying}>
                {p.art ? <img src={p.art} style={styles.libArt} /> : <div style={styles.libArt}><FaListUl /></div>}
                <div style={styles.text}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{p.name}</div>
                  <div style={styles.subtitle}>{p.count} songs{p.downloaded ? ` · ${p.downloaded} offline` : ""}</div>
                </div>
              </div>
            } />
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={openNewPlaylist}><FaPlus style={{ marginRight: 6 }} />New playlist</ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

// Music-only volume (system volume is separate). Live while sliding.
function MusicVolume({ volume }: { volume: number }) {
  // The slider shows what you set; it only follows the player again once
  // you've stopped touching it for a moment (no snapping back mid-drag).
  const [draft, setDraft] = useState<number | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const release = useRef<number | undefined>(undefined);
  useEffect(() => () => { window.clearTimeout(timer.current); window.clearTimeout(release.current); }, []);
  const onChange = (value: number) => {
    setDraft(value);
    window.clearTimeout(timer.current);
    window.clearTimeout(release.current);
    timer.current = window.setTimeout(() => {
      api.setVolume(value).catch(() => {}).finally(() => {
        release.current = window.setTimeout(() => setDraft(null), 1500);
      });
    }, 80);
  };
  // Our own icon + slider row: SliderField's built-in icon slot reserves a
  // label column and padding, which pushed the slider off-centre.
  const muted = (draft ?? volume) === 0;
  return (
    <PanelSectionRow>
      <div style={styles.volumeRow}>
        <div style={styles.volumeIcon}>{muted ? <MdVolumeOff size={20} /> : <MdVolumeUp size={20} />}</div>
        {/* Steam's slider has a 270px min-width that overflows the sidebar; let it shrink. */}
        <style>{`.cuivar-volume * { min-width: 0 !important; }`}</style>
        <div className="cuivar-volume" style={{ flex: 1, minWidth: 0, marginRight: 12 }}>
          <SliderField
            value={draft ?? volume}
            min={0}
            max={100}
            step={5}
            showValue={false}
            onChange={onChange}
            bottomSeparator="none"
          />
        </div>
      </div>
    </PanelSectionRow>
  );
}

function PlayerTab({ state, spoken }: { state: PlayerState | null; spoken: { text: string; n: number } | null }) {
  return (
    <>
      <PanelSection>
        {state?.track
          ? <><NowPlaying state={state} /><MusicVolume volume={state.volume} /></>
          : <PanelSectionRow><div style={styles.note}>Search for a song to start. It keeps playing while you game.</div></PanelSectionRow>}
      </PanelSection>
      <Search spoken={spoken} />
      {state && state.upNext.length > 0 && (
        <PanelSection title="Up next">
          <ExpandableList
            tracks={state.upNext}
            keyPrefix="u"
            resetKey={state.track?.id ?? ""}
            onPlay={(_track, i) => api.playAt(state.index + 1 + i)}
          />
        </PanelSection>
      )}
    </>
  );
}

function Panel() {
  const state = usePlayerState();
  const [info] = useLibrary();
  const [tab, setTab] = useState("player");
  const voice = useVoiceSearch(() => setTab("player"));

  // Messages from the background player (download problems, limit reached).
  useEffect(() => {
    const listener = addEventListener<[string]>("notice", text => toast(text));
    return () => removeEventListener("notice", listener);
  }, []);

  // Our own two-button tab bar: no L1/R1 hints, no fixed height, the panel
  // scrolls as one page. D-pad left/right moves between the two tabs.
  return (
    <Focusable
      onButtonDown={voice.onButtonDown as any}
      onButtonUp={voice.onButtonUp as any}
      onOptionsButton={() => {}}
      onOptionsActionDescription="Hold: voice search"
    >
      <style>{`@keyframes cuivar-listen { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.25); opacity: 0.6; } }`}</style>
      {voice.banner && (
        <div style={styles.voiceBanner}>
          <span style={voice.phase === "listening" ? styles.voiceMic : { display: "flex", color: "#1a9fff" }}><MdMic size={20} /></span>
          <span>{voice.banner}</span>
        </div>
      )}
      {state && state.engine.state !== "ready" && <EngineBanner engine={state.engine} />}
      <PanelSection>
        <Focusable style={styles.tabBar} flow-children="horizontal">
          {([["player", "Player"], ["library", "Library"]] as const).map(([id, title]) => (
            <TabButton key={id} active={tab === id} onClick={() => setTab(id)}>{title}</TabButton>
          ))}
        </Focusable>
      </PanelSection>
      {tab === "player" ? <PlayerTab state={state} spoken={voice.spoken} /> : <Library info={info} state={state} />}
    </Focusable>
  );
}

// First launch downloads the music engine; it also updates itself when
// YouTube changes something. Shown at the top of the panel while that happens.
function EngineBanner({ engine }: { engine: PlayerState["engine"] }) {
  const text = engine.state === "setting_up"
    ? `Setting up music engine… ${Math.round(engine.progress * 100)}%`
    : engine.state === "updating" ? "Updating music engine…"
    : engine.message || "The music engine isn't working.";
  return (
    <PanelSection>
      <PanelSectionRow>
        <div style={engine.state === "error" ? styles.warn : styles.note}>
          {engine.state !== "error" && <span style={styles.engineSpin}><MdDownloading size={14} /></span>}
          {text}
        </div>
      </PanelSectionRow>
      {engine.state === "error" && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => api.engineSetup()}>Try again</ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

type VoiceEvent = CustomEvent<{ button: number }>;

// Hold Y anywhere in the panel to talk; let go to search. The first time,
// offers the one-time download of the speech engine.
function useVoiceSearch(onHeard: () => void) {
  const [phase, setPhaseState] = useState<"idle" | "listening" | "thinking" | "downloading">("idle");
  const phaseRef = useRef(phase);
  const setPhase = (next: typeof phase) => { phaseRef.current = next; setPhaseState(next); };
  const [progress, setProgress] = useState(0);
  const [spoken, setSpoken] = useState<{ text: string; n: number } | null>(null);
  const holding = useRef(false);
  const starting = useRef<Promise<boolean> | null>(null);

  const watchDownload = () => {
    setPhase("downloading");
    const poll = () => api.voiceStatus().then(v => {
      if (v.downloading) {
        setProgress(v.progress);
        window.setTimeout(poll, 700);
        return;
      }
      setPhase("idle");
      if (v.ready) toast("Voice search is ready. Hold Y and say a song.");
      else if (v.error) toast(v.error);
    }).catch(() => setPhase("idle"));
    poll();
  };

  const offerDownload = (sizeMb: number) => showModal(
    <ConfirmModal
      strTitle="Voice search"
      strDescription={`Hold Y, say a song, let go. It needs a one-time ${sizeMb} MB download. Your voice is turned into text on this device and never leaves it.`}
      strOKButtonText="Download"
      onOK={() => { api.voicePrepare().then(watchDownload); }}
    />,
  );

  const start = async () => {
    const status = await api.voiceStatus();
    if (status.downloading) { watchDownload(); return false; }
    if (!status.ready) { offerDownload(status.sizeMb); return false; }
    if (!holding.current) return false;  // let go before we even started
    setPhase("listening");
    const result = await api.voiceStart();
    if (!result.ok) { setPhase("idle"); toast(result.error); return false; }
    return true;
  };

  const stop = async () => {
    const listening = await starting.current;
    starting.current = null;
    if (!listening) return;
    setPhase("thinking");
    const result = await api.voiceStop().catch(() => ({ ok: false as const, error: "Voice search failed." }));
    setPhase("idle");
    if (result.ok) {
      onHeard();
      setSpoken(previous => ({ text: result.text, n: (previous?.n ?? 0) + 1 }));
    } else {
      toast(result.error);
    }
  };

  const isY = (event: VoiceEvent) => event.detail?.button === GamepadButton.OPTIONS;
  const onButtonDown = (event: VoiceEvent) => {
    if (!isY(event) || holding.current || phaseRef.current === "thinking" || phaseRef.current === "downloading") return;
    holding.current = true;
    starting.current = start().catch(() => false);
  };
  const onButtonUp = (event: VoiceEvent) => {
    if (!isY(event) || !holding.current) return;
    holding.current = false;
    stop();
  };

  const banner = phase === "listening" ? "Listening… let go of Y to search"
    : phase === "thinking" ? "Getting that…"
    : phase === "downloading" ? `Downloading voice search… ${Math.round(progress * 100)}%`
    : null;
  return { phase, banner, spoken, onButtonDown, onButtonUp };
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  const [focused, setFocused] = useState(false);
  return (
    <div style={{ flex: 1 }} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}>
      <DialogButton
        onClick={onClick}
        onGamepadFocus={onClick}
        style={{
          ...styles.tab,
          ...(active ? styles.tabActive : null),
          ...(focused ? styles.tabFocused : null),
        }}
      >
        {children}
      </DialogButton>
    </div>
  );
}

function TitleBar() {
  return (
    <Focusable style={styles.titleBar} flow-children="horizontal">
      <div className={staticClasses.Title} style={{ padding: 0 }}>Cuivar Pulse</div>
      <DialogButton style={styles.gear} onClick={openSettings} aria-label="Settings">
        <FaCog />
      </DialogButton>
    </Focusable>
  );
}


export default definePlugin(() => {
  // Fullscreen (Music: the artwork, TV: the pixel video) is its own page.
  routerHook.addRoute(FULLSCREEN_ROUTE, Fullscreen, { exact: true });
  return {
    name: "Cuivar Pulse",
    titleView: <TitleBar />,
    content: <Panel />,
    icon: <FaMusic />,
    onDismount() {
      routerHook.removeRoute(FULLSCREEN_ROUTE);
    },
  };
});

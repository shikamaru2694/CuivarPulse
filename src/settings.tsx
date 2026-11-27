// Settings pop-up (opened from the ⚙ in the title bar): download storage,
// region and the music engine.

import { ModalRoot, showModal, SliderField, DropdownItem, ButtonItem, Field, ToggleField } from "@decky/ui";
import { useEffect, useRef, useState, CSSProperties } from "react";
import { api, HapticsMode, LibraryInfo, LightColours, LightSettings, PixelStyle, Region, VoiceStatus, formatBytes, useLibrary } from "./api";
import { toast, openStaleReview } from "./modals";

const s: Record<string, CSSProperties> = {
  heading: { fontSize: 20, fontWeight: 700, marginBottom: 6 },
  section: { fontSize: 12, fontWeight: 700, letterSpacing: 1, opacity: 0.7, margin: "18px 0 4px", textTransform: "uppercase" },
  note: { fontSize: 12, opacity: 0.7, lineHeight: 1.4 },
  warn: { fontSize: 12, lineHeight: 1.4 },
};

export function openSettings() {
  showModal(<Settings />);
}

type Regions = { selected: string; auto: string; regions: Region[] };
type EngineInfo = { version: string | null; lastError: { message: string } | null; kind?: "python" | "binary" | null };
type Loaded = { lights: LightSettings | null; voice: VoiceStatus | null; regions: Regions | null; engine: EngineInfo | null };

const settled = <T,>(result: PromiseSettledResult<T>) => (result.status === "fulfilled" ? result.value : null);

function Settings({ closeModal }: { closeModal?: () => void }) {
  const [info] = useLibrary();
  // Everything is loaded first and the sections appear together: Steam
  // selects the first button that exists when the dialog opens, so sections
  // popping in one by one left the controller on the last one, at the bottom.
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    Promise.allSettled([api.getLights(), api.voiceStatus(), api.getRegions(), api.getEngine()])
      .then(([lights, voice, regions, engine]) => setLoaded({
        lights: settled(lights), voice: settled(voice), regions: settled(regions), engine: settled(engine),
      }));
  }, []);
  return (
    <ModalRoot onCancel={closeModal}>
      <div style={s.heading}>Cuivar Pulse settings</div>
      {/* No scroll box of our own: Steam's dialog scrolls to whatever the
          controller selects, but not inside a nested scroll area. */}
      {!loaded || !info ? <div style={s.note}>Loading…</div> : (
        <>
          <LightsSettings initial={loaded.lights} />
          <VoiceSettings initial={loaded.voice} />
          <PixelSettings info={info} />
          <StorageSettings info={info} />
          <RegionSettings initial={loaded.regions} />
          <EngineSettings initial={loaded.engine} />
        </>
      )}
    </ModalRoot>
  );
}

// --- controller lights -------------------------------------------------------------

const COLOUR_OPTIONS: { data: LightColours; label: string }[] = [
  { data: "artwork", label: "Artwork" },
  { data: "rainbow", label: "Rainbow" },
  { data: "fixed", label: "One colour" },
];

const COLOUR_HINTS: Record<LightColours, string> = {
  artwork: "Colours from the song's artwork, moving on at the big moments. In TV mode they follow the video, like Ambilight.",
  rainbow: "Flows through every colour: slowly when the song is calm, faster as it gets intense.",
  fixed: "Always the colour you pick.",
};

const FIXED_COLOURS = [
  { data: "#1a9fff", label: "Steam blue" },
  { data: "#ff2a2a", label: "Red" },
  { data: "#ff7a00", label: "Orange" },
  { data: "#ffd000", label: "Yellow" },
  { data: "#20e050", label: "Green" },
  { data: "#00e5ff", label: "Cyan" },
  { data: "#8a3cff", label: "Purple" },
  { data: "#ff3cc8", label: "Pink" },
  { data: "#ffffff", label: "White" },
];

const HAPTIC_OPTIONS: { data: HapticsMode; label: string }[] = [
  { data: "off", label: "Off" },
  { data: "subtle", label: "Subtle" },
  { data: "strong", label: "Strong" },
];

const HAPTIC_HINTS: Record<HapticsMode, string> = {
  off: "Your controller taps along to the music. Turn it off if a game uses rumble.",
  subtle: "A short tap on the biggest beats and drops.",
  strong: "Every beat, with the drops hitting harder. Uses more battery.",
};

function LightsSettings({ initial }: { initial: LightSettings | null }) {
  const [lights, setLights] = useState<LightSettings | null>(initial);
  // Lights only on devices with ring lights (Legion Go S); taps on handhelds with rumble.
  if (!lights || (!lights.available && !lights.hapticsAvailable)) return null;

  const changeColours = (colours: LightColours, fixed: string) => {
    setLights({ ...lights, colours, fixed });
    api.setLightColours(colours, fixed).catch(() => {});
  };

  return (
    <>
      <div style={s.section}>Controller</div>
      {lights.available && <ToggleField
        label="Lights dance to the music"
        description="The joystick rings pulse to the beat. Your own lighting comes back when the music stops."
        checked={lights.enabled}
        onChange={on => {
          setLights({ ...lights, enabled: on });
          api.setLights(on).then(setLights).catch(() => {});
        }}
      />}
      {lights.available && lights.enabled && (
        <>
          <DropdownItem
            label="Light colours"
            description={COLOUR_HINTS[lights.colours]}
            rgOptions={COLOUR_OPTIONS}
            selectedOption={lights.colours}
            onChange={option => changeColours(option.data, lights.fixed)}
          />
          {lights.colours === "fixed" && (
            <DropdownItem
              label="Colour"
              rgOptions={FIXED_COLOURS.map(c => ({
                data: c.data,
                label: <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 12, height: 12, borderRadius: 6, background: c.data, display: "inline-block" }} />
                  {c.label}
                </span>,
              }))}
              selectedOption={lights.fixed}
              onChange={option => changeColours("fixed", option.data)}
            />
          )}
        </>
      )}
      {lights.hapticsAvailable && (
        <DropdownItem
          label="Feel the beat"
          description={HAPTIC_HINTS[lights.haptics]}
          rgOptions={HAPTIC_OPTIONS}
          selectedOption={lights.haptics}
          onChange={option => {
            setLights({ ...lights, haptics: option.data });
            api.setHaptics(option.data).then(setLights).catch(() => {});
          }}
        />
      )}
    </>
  );
}

// --- voice search ------------------------------------------------------------------

function VoiceSettings({ initial }: { initial: VoiceStatus | null }) {
  const [voice, setVoice] = useState<VoiceStatus | null>(initial);
  useEffect(() => {
    let alive = true;
    const poll = () => api.voiceStatus().then(v => {
      if (!alive) return;
      setVoice(v);
      if (v.downloading) window.setTimeout(poll, 700);
    }).catch(() => {});
    poll();
    return () => { alive = false; };
  }, [voice?.downloading]);
  if (!voice) return null;

  const description = voice.downloading
    ? `Downloading… ${Math.round(voice.progress * 100)}%`
    : voice.ready
      ? "Hold Y anywhere in Cuivar Pulse and say a song. Your voice is turned into text on this device and never leaves it."
      : voice.error ?? `Hold Y and say a song. Needs a one-time ${voice.sizeMb} MB download; your voice never leaves this device.`;
  return (
    <>
      <div style={s.section}>Voice search</div>
      <Field label={voice.ready ? "Ready" : "Not downloaded"} description={description} />
      {voice.ready
        ? <ButtonItem layout="below" onClick={() => api.voiceRemove().then(setVoice)}>Remove voice search</ButtonItem>
        : !voice.downloading && <ButtonItem layout="below" onClick={() => api.voicePrepare().then(setVoice)}>
            Download ({voice.sizeMb} MB)
          </ButtonItem>}
    </>
  );
}

// --- pixel video -------------------------------------------------------------------

const PIXEL_STYLES: { data: PixelStyle; label: string; hint: string }[] = [
  { data: "retro", label: "Retro", hint: "32 classic game colours with a light dither, like an old console game." },
  { data: "arcade", label: "Arcade", hint: "The video's own colours in clean square pixels, with CRT scanlines in fullscreen." },
  { data: "gameboy", label: "Game Boy", hint: "Four shades of green." },
];

type PixelDraft = { save: boolean; style: PixelStyle };

function PixelSettings({ info }: { info: LibraryInfo | null }) {
  const [draft, setDraft] = useState<PixelDraft | null>(
    info ? { save: info.settings.pixelSave, style: info.settings.pixelStyle } : null);
  useEffect(() => {
    if (info) setDraft({ save: info.settings.pixelSave, style: info.settings.pixelStyle });
  }, [info?.settings.pixelSave, info?.settings.pixelStyle]);
  if (!draft) return null;
  const save = (next: PixelDraft) => {
    setDraft(next);
    api.setPixelSettings(next.save, next.style).catch(() => {});
  };
  return (
    <>
      <div style={s.section}>TV mode</div>
      <DropdownItem
        label="Pixel style"
        description={PIXEL_STYLES.find(o => o.data === draft.style)?.hint}
        rgOptions={PIXEL_STYLES.map(({ data, label }) => ({ data, label }))}
        selectedOption={draft.style}
        onChange={option => save({ ...draft, style: option.data })}
      />
      <ToggleField
        label="Save pixel video with downloads"
        description="Downloaded songs also keep their small pixel video (about 1–2 MB each), so it plays offline."
        checked={draft.save}
        onChange={on => save({ ...draft, save: on })}
      />
    </>
  );
}

// --- storage --------------------------------------------------------------------

const MODE_OPTIONS = [
  { data: "songs", label: "Songs" },
  { data: "storage", label: "Storage" },
];

// Download allowance steps (notched slider); the backend snaps to the same.
const SONG_STEPS = [50, 100, 200, 500];
const GB_STEPS = [2, 5, 10, 25];

const nearestStep = (steps: number[], value: number) =>
  steps.reduce((best, step, i) => (Math.abs(step - value) < Math.abs(steps[best] - value) ? i : best), 0);

function StorageSettings({ info }: { info: LibraryInfo | null }) {
  // Local copy while sliding; saved shortly after the slider stops moving.
  const [draft, setDraft] = useState(info?.settings ?? null);
  const saveTimer = useRef<number | undefined>(undefined);
  useEffect(() => { if (info) setDraft(info.settings); }, [info?.settings.limitMode, info?.settings.limitSongs, info?.settings.limitGB]);
  if (!info || !draft) return <div style={s.note}>Loading…</div>;

  const save = (next: typeof draft) => {
    setDraft(next);
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      api.setSettings(next.limitMode, next.limitSongs, next.limitGB);
    }, 500);
  };

  const { usage } = info;
  const bySongs = draft.limitMode === "songs";
  const steps = bySongs ? SONG_STEPS : GB_STEPS;
  const stepIndex = nearestStep(steps, bySongs ? draft.limitSongs : draft.limitGB);
  const limitText = bySongs ? `${steps[stepIndex]} songs` : `${steps[stepIndex]} GB`;
  const usedText = bySongs
    ? `${usage.songs} of ${steps[stepIndex]} songs used`
    : `${formatBytes(usage.bytes)} of ${steps[stepIndex]} GB used`;

  const changeMode = (mode: "songs" | "storage") => save({
    ...draft,
    limitMode: mode,
    limitSongs: SONG_STEPS[nearestStep(SONG_STEPS, draft.limitSongs)],
    limitGB: GB_STEPS[nearestStep(GB_STEPS, draft.limitGB)],
  });

  const changeStep = (index: number) => save(bySongs
    ? { ...draft, limitSongs: SONG_STEPS[index] }
    : { ...draft, limitGB: GB_STEPS[index] });

  return (
    <>
      <div style={s.section}>Downloads</div>
      <DropdownItem
        label="Download limit"
        rgOptions={MODE_OPTIONS}
        selectedOption={draft.limitMode}
        onChange={option => changeMode(option.data)}
      />
      <SliderField
        label={`Allowance: ${limitText}`}
        value={stepIndex}
        min={0}
        max={steps.length - 1}
        step={1}
        notchCount={steps.length}
        notchTicksVisible
        notchLabels={steps.map((value, i) => ({ notchIndex: i, label: bySongs ? String(value) : `${value} GB`, value: i }))}
        showValue={false}
        onChange={changeStep}
      />
      <div style={s.note}>{usedText} ({Math.round(usage.ratio * 100)}%). Downloads are never removed without asking you.</div>
      {(usage.nearLimit || usage.staleCount > 0) && (
        <div style={{ ...s.warn, marginTop: 8 }}>
          {usage.nearLimit ? "You're close to your limit. " : ""}
          {usage.staleCount > 0
            ? `${usage.staleCount} download${usage.staleCount === 1 ? " hasn't" : "s haven't"} been played in 30+ days.`
            : ""}
        </div>
      )}
      {usage.staleCount > 0 && <ButtonItem layout="below" onClick={openStaleReview}>Review songs you don't play</ButtonItem>}
      <div style={{ ...s.note, marginTop: 10 }}>Automatic cache: {formatBytes(info.cacheBytes)} (recently streamed songs; cleans itself up)</div>
      <ButtonItem layout="below" onClick={() => api.clearCache().then(() => toast("Cache cleared."))}>Clear cache</ButtonItem>
    </>
  );
}

// --- region ------------------------------------------------------------------------

function RegionSettings({ initial }: { initial: Regions | null }) {
  const [regions, setRegions] = useState<Regions | null>(initial);
  if (!regions) return null;

  const autoName = regions.regions.find(r => r.code === regions.auto)?.name ?? regions.auto;
  const options = [
    { data: "auto", label: `Automatic (${autoName})` },
    ...regions.regions.map(r => ({ data: r.code, label: r.name })),
  ];

  return (
    <>
      <div style={s.section}>Region</div>
      <DropdownItem
        label="Country"
        description="Search results and recommendations follow this country's YouTube Music."
        rgOptions={options}
        selectedOption={regions.selected}
        onChange={option => api.setRegion(option.data).then(result => {
          setRegions({ ...regions, selected: option.data });
          if (result.ok) toast("Region updated.");
        })}
      />
    </>
  );
}

// --- music engine (yt-dlp) ---------------------------------------------------------------

function EngineSettings({ initial }: { initial: EngineInfo | null }) {
  const [engine, setEngine] = useState<EngineInfo | null>(initial);
  const [updating, setUpdating] = useState(false);

  const update = async () => {
    setUpdating(true);
    const result = await api.updateEngine().catch(() => ({ ok: false as const, error: "Update failed." }));
    setUpdating(false);
    if (result.ok) {
      toast(result.message || "Music engine updated.");
      setEngine({ ...engine, version: result.version, lastError: null });
    } else {
      toast(result.error);
    }
  };

  return (
    <>
      <div style={s.section}>Music engine</div>
      <Field
        label="Version"
        description="yt-dlp, from its official releases. It checks for updates once a day and updates itself when YouTube changes something; this button is a backup."
        bottomSeparator="none"
      >
        <span style={{ fontSize: 13, opacity: 0.85 }}>
          {engine?.version ?? "not set up yet"}{engine?.kind === "binary" ? " (Linux build)" : ""}
        </span>
      </Field>
      {engine?.lastError && <div style={{ ...s.warn, color: "#ffb4a8" }}>Last problem: {engine.lastError.message}</div>}
      <ButtonItem layout="below" onClick={update} disabled={updating}>
        {updating ? "Updating…" : "Update music engine"}
      </ButtonItem>
    </>
  );
}

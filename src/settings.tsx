// Settings pop-up (opened from the ⚙ in the title bar): download storage,
// region and the music engine.

import { ModalRoot, showModal, SliderField, DropdownItem, ButtonItem, Field, ToggleField } from "@decky/ui";
import { useEffect, useRef, useState, CSSProperties } from "react";
import { api, HapticsMode, LibraryInfo, LightColours, LightSettings, Region, VoiceStatus, formatBytes, useLibrary } from "./api";
import { toast, openStaleReview } from "./modals";

const s: Record<string, CSSProperties> = {
  heading: { fontSize: 20, fontWeight: 700, marginBottom: 6 },
  section: { fontSize: 12, fontWeight: 700, letterSpacing: 1, opacity: 0.7, margin: "18px 0 4px", textTransform: "uppercase" },
  note: { fontSize: 12, opacity: 0.7, lineHeight: 1.4 },
  warn: { fontSize: 12, lineHeight: 1.4 },
  scroll: { maxHeight: "70vh", overflowY: "auto", paddingRight: 4 },
};

export function openSettings() {
  showModal(<Settings />);
}

function Settings({ closeModal }: { closeModal?: () => void }) {
  const [info] = useLibrary();
  return (
    <ModalRoot onCancel={closeModal}>
      <div style={s.heading}>Cuivar Pulse settings</div>
      <div style={s.scroll}>
        <LightsSettings />
        <VoiceSettings />
        <StorageSettings info={info} />
        <RegionSettings />
        <EngineSettings />
      </div>
    </ModalRoot>
  );
}

// --- controller lights -------------------------------------------------------------

const COLOUR_OPTIONS: { data: LightColours; label: string }[] = [
  { data: "artwork", label: "Artwork" },
  { data: "energy", label: "Energy" },
  { data: "rainbow", label: "Rainbow" },
  { data: "fixed", label: "One colour" },
];

const COLOUR_HINTS: Record<LightColours, string> = {
  artwork: "Colours from the song's artwork, moving to the next one at the big moments.",
  energy: "Cool blues when the music is calm, warm reds when it gets intense.",
  rainbow: "Slowly flows through every colour.",
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

function LightsSettings() {
  const [lights, setLights] = useState<LightSettings | null>(null);
  useEffect(() => { api.getLights().then(setLights).catch(() => {}); }, []);
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

function VoiceSettings() {
  const [voice, setVoice] = useState<VoiceStatus | null>(null);
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

function RegionSettings() {
  const [regions, setRegions] = useState<{ selected: string; auto: string; regions: Region[] } | null>(null);
  useEffect(() => { api.getRegions().then(setRegions).catch(() => {}); }, []);
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

function EngineSettings() {
  const [engine, setEngine] = useState<{ version: string | null; lastError: { message: string } | null } | null>(null);
  const [updating, setUpdating] = useState(false);
  useEffect(() => { api.getEngine().then(setEngine).catch(() => {}); }, []);

  const update = async () => {
    setUpdating(true);
    const result = await api.updateEngine().catch(() => ({ ok: false as const, error: "Update failed." }));
    setUpdating(false);
    if (result.ok) {
      toast(result.message || "Music engine updated.");
      setEngine({ version: result.version, lastError: null });
    } else {
      toast(result.error);
    }
  };

  return (
    <>
      <div style={s.section}>Music engine</div>
      <Field
        label="Version"
        description="If songs stop loading because YouTube changed something, updating the engine usually fixes it."
        bottomSeparator="none"
      >
        <span style={{ fontSize: 13, opacity: 0.85 }}>{engine?.version ?? "…"}</span>
      </Field>
      {engine?.lastError && <div style={{ ...s.warn, color: "#ffb4a8" }}>Last problem: {engine.lastError.message}</div>}
      <ButtonItem layout="below" onClick={update} disabled={updating}>
        {updating ? "Updating…" : "Update music engine"}
      </ButtonItem>
    </>
  );
}

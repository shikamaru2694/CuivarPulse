// Commands sent to the background player (main.py) and the shapes it returns.

import { callable, addEventListener, removeEventListener } from "@decky/api";
import { useEffect, useState } from "react";

export type Artist = { name: string; id: string | null };
export type Track = {
  kind: "track";
  id: string;
  title: string;
  artists: Artist[];
  album: { name: string; id: string | null } | null;
  duration: number | null;
  art: string | null;
  downloaded?: boolean;
  size?: number;
  added?: number;
  lastPlayed?: number | null;
};
export type PlayerState = {
  track: Track | null;
  playing: boolean;
  loading: boolean;
  position: number;
  duration: number;
  index: number;
  upNext: Track[];
  error: string | null;
  downloaded: boolean;
  downloadingId: string | null;
  pendingIds: string[];
  shuffle: boolean;
  repeat: "off" | "all" | "one";
  volume: number;
  pixelVideo: boolean;   // TV mode (vs Music mode)
  outputDelay: number;   // seconds until the sound is heard (Bluetooth ~0.2)
  engine: EngineStatus;
};
// The music engine (yt-dlp + QuickJS), downloaded on first launch and kept current.
export type EngineStatus = {
  state: "ready" | "setting_up" | "updating" | "error"; progress: number; message: string | null;
  version: string | null; kind: "python" | "binary" | null;
};
export type PixelSize = 64 | 96;  // Medium (panel) / Fine (fullscreen)
export type PixelStyle = "retro" | "arcade" | "gameboy";
export type PixelStatus = { state: "idle" | "finding" | "fetching" | "preparing" | "ready" | "none" };
export type Region = { code: string; name: string };
export type HapticsMode = "off" | "subtle" | "strong";
export type LightColours = "artwork" | "rainbow" | "fixed";
export type LightSettings = {
  available: boolean; enabled: boolean; colours: LightColours; fixed: string;
  hapticsAvailable: boolean; haptics: HapticsMode;
};
export type VoiceStatus = {
  ready: boolean; downloading: boolean; progress: number; error: string | null; sizeMb: number; listening: boolean;
};
export type PlaylistSummary = { id: string; name: string; count: number; downloaded: number; art: string | null };
export type Playlist = { id: string; name: string; tracks: Track[] };
export type Settings = {
  limitMode: "songs" | "storage"; limitSongs: number; limitGB: number; region: string;
  pixelSave: boolean; pixelStyle: PixelStyle;
};
export type Usage = { songs: number; bytes: number; ratio: number; nearLimit: boolean; staleCount: number };
export type LibraryInfo = { playlists: PlaylistSummary[]; downloads: number; settings: Settings; usage: Usage; cacheBytes: number };
export type Result<T = {}> = ({ ok: true } & T) | { ok: false; error: string };

export const api = {
  getState: callable<[], PlayerState>("get_state"),
  search: callable<[string], Result<{ results: Track[] }>>("search"),
  playTrack: callable<[Track], void>("play_track"),
  playList: callable<[Track[], number, boolean], void>("play_list"),
  playNext: callable<[Track], void>("play_next"),
  playAt: callable<[number], void>("play_at"),
  toggle: callable<[], void>("toggle"),
  next: callable<[], void>("next"),
  previous: callable<[], void>("previous"),
  seek: callable<[number], void>("seek"),
  seekBy: callable<[number], void>("seek_by"),
  setShuffle: callable<[boolean], PlayerState>("set_shuffle"),
  cycleRepeat: callable<[], PlayerState>("cycle_repeat"),

  getLibrary: callable<[], LibraryInfo>("get_library"),
  getPlaylist: callable<[string], Result<{ playlist: Playlist }>>("get_playlist"),
  createPlaylist: callable<[string, Track | null], Result<{ id: string }>>("create_playlist"),
  renamePlaylist: callable<[string, string], Result>("rename_playlist"),
  deletePlaylist: callable<[string], Result>("delete_playlist"),
  addToPlaylist: callable<[string, Track], Result<{ added: boolean }>>("add_to_playlist"),
  removeFromPlaylist: callable<[string, string], Result>("remove_from_playlist"),

  getDownloads: callable<[], { tracks: Track[]; stale: Track[]; usage: Usage }>("get_downloads"),
  downloadTracks: callable<[Track[]], Result<{ added: number }>>("download_tracks"),
  downloadPlaylist: callable<[string], Result<{ added: number }>>("download_playlist"),
  removeDownloads: callable<[string[]], Result>("remove_downloads"),
  cancelDownloads: callable<[], Result>("cancel_downloads"),

  setSettings: callable<[string, number, number], Result<{ settings: Settings }>>("set_settings"),
  clearCache: callable<[], Result>("clear_cache"),
  setVolume: callable<[number], number>("set_volume"),
  getRegions: callable<[], { selected: string; auto: string; regions: Region[] }>("get_regions"),
  setRegion: callable<[string], Result<{ region: string }>>("set_region"),
  getEngine: callable<[], { version: string | null; lastError: { code: string; message: string } | null }>("get_engine"),
  updateEngine: callable<[], Result<{ message: string; version: string | null }>>("update_engine"),

  getLights: callable<[], LightSettings>("get_lights"),
  setLights: callable<[boolean], LightSettings>("set_lights"),
  setHaptics: callable<[HapticsMode], LightSettings>("set_haptics"),
  voiceStatus: callable<[], VoiceStatus>("voice_status"),
  voicePrepare: callable<[], VoiceStatus>("voice_prepare"),
  voiceRemove: callable<[], VoiceStatus>("voice_remove"),
  voiceStart: callable<[], Result>("voice_start"),
  voiceStop: callable<[], Result<{ text: string }>>("voice_stop"),
  setLightColours: callable<[LightColours, string], Result<LightSettings>>("set_light_colours"),
  engineSetup: callable<[], EngineStatus>("engine_setup"),
  pixelWatch: callable<[string], PixelStatus>("pixel_watch"),
  pixelGet: callable<[string, PixelSize], Result<{ data: string; size: PixelSize }>>("pixel_get"),
  setPixelVideo: callable<[boolean], Result<{ on: boolean }>>("set_pixel_video"),
  setPixelSettings: callable<[boolean, PixelStyle], Result<{ settings: Settings }>>("set_pixel_settings"),

};

export const formatTime = (seconds: number) => {
  const s = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

export const formatBytes = (bytes: number) =>
  bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;

export const artistNames = (track: Track) => track.artists.map(a => a.name).join(", ");

// Live player state, pushed by the backend every second while playing.
export function usePlayerState(): PlayerState | null {
  const [state, setState] = useState<PlayerState | null>(null);
  useEffect(() => {
    api.getState().then(setState).catch(() => {});
    const listener = addEventListener<[PlayerState]>("state", setState);
    return () => removeEventListener("state", listener);
  }, []);
  return state;
}

// Library summary; refreshed whenever the backend says the library changed.
export function useLibrary(): [LibraryInfo | null, () => void] {
  const [info, setInfo] = useState<LibraryInfo | null>(null);
  const refresh = () => { api.getLibrary().then(setInfo).catch(() => {}); };
  useEffect(() => {
    refresh();
    const listener = addEventListener<[]>("library", refresh);
    return () => removeEventListener("library", listener);
  }, []);
  return [info, refresh];
}

// Runs `load` now and again on every "library" change event.
export function useLibraryReload(load: () => void) {
  useEffect(() => {
    load();
    const listener = addEventListener<[]>("library", load);
    return () => removeEventListener("library", listener);
  }, []);
}

// Pop-ups opened from the sidebar: song options, playlists, downloads.
// Every list row is a focusable Field so the D-pad can land on it;
// A = main action, X = secondary action (shown in Steam's footer).

import { ModalRoot, showModal, ConfirmModal, Field, DialogButton, Focusable, TextField } from "@decky/ui";
import { toaster } from "@decky/api";
import { useState, CSSProperties, ReactNode } from "react";
import { FaDownload, FaCheck, FaPlay, FaRandom, FaPlus, FaTrash, FaListUl, FaStepForward } from "react-icons/fa";
import { api, Track, Playlist, PlaylistSummary, Usage, artistNames, formatTime, formatBytes, useLibraryReload } from "./api";

type ModalProps = { closeModal?: () => void };

export const toast = (body: string) => toaster.toast({ title: "Cuivar Pulse", body });

const s: Record<string, CSSProperties> = {
  line: { display: "flex", gap: 10, alignItems: "center", width: "100%", minWidth: 0, paddingLeft: 8, boxSizing: "border-box" },
  art: { width: 40, height: 40, borderRadius: 4, objectFit: "cover", background: "#1d1d1d", flexShrink: 0 },
  text: { minWidth: 0, display: "flex", flexDirection: "column", gap: 2, flex: 1 },
  title: { fontWeight: 600, fontSize: 13, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  subtitle: { fontSize: 12, opacity: 0.7, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  badge: { fontSize: 12, opacity: 0.8, flexShrink: 0 },
  buttons: { display: "flex", gap: 8, flexWrap: "wrap", margin: "8px 0 12px" },
  button: { flex: "1 1 auto", minWidth: 0, display: "flex", alignItems: "center", justifyContent: "center", gap: 8 },
  stack: { display: "flex", flexDirection: "column", gap: 8 },
  list: { maxHeight: "55vh", overflowY: "auto" },
  heading: { fontSize: 20, fontWeight: 700, marginBottom: 4 },
  note: { fontSize: 13, opacity: 0.75 },
};

export function TrackLine({ track, badge, onArtClick }: { track: Track; badge?: ReactNode; onArtClick?: () => void }) {
  const art = track.art ? <img src={track.art} style={s.art} /> : <div style={s.art} />;
  return (
    <div style={s.line}>
      {onArtClick
        ? <div onClick={e => { e.stopPropagation(); onArtClick(); }}>{art}</div>
        : art}
      <div style={s.text}>
        <div style={s.title}>{track.title}</div>
        <div style={s.subtitle}>{artistNames(track)}{track.duration ? ` · ${formatTime(track.duration)}` : ""}</div>
      </div>
      {badge && <div style={s.badge}>{badge}</div>}
    </div>
  );
}

function Row({ children, onActivate, onSecondary, secondaryLabel }: {
  children: ReactNode; onActivate: () => void; onSecondary?: () => void; secondaryLabel?: string;
}) {
  return (
    <Field
      focusable
      highlightOnFocus
      padding="compact"
      bottomSeparator="none"
      onActivate={onActivate}
      onClick={onActivate}
      onSecondaryButton={onSecondary}
      onSecondaryActionDescription={secondaryLabel}
      label={children}
    />
  );
}

function Btn({ icon, label, onClick, disabled }: { icon?: ReactNode; label: string; onClick: () => void; disabled?: boolean }) {
  return <DialogButton style={s.button} onClick={onClick} disabled={disabled}>{icon}{label}</DialogButton>;
}

// --- song options -------------------------------------------------------------------

export function openSongOptions(track: Track) {
  showModal(<SongOptions track={track} />);
}

function SongOptions({ track, closeModal }: { track: Track } & ModalProps) {
  const [downloaded, setDownloaded] = useState<boolean | null>(null);
  useLibraryReload(() => {
    api.getDownloads().then(d => setDownloaded(d.tracks.some(t => t.id === track.id))).catch(() => {});
  });

  const run = (action: () => Promise<unknown>, message?: string) => {
    action().then(() => { if (message) toast(message); }).catch(() => toast("Something went wrong."));
    closeModal?.();
  };

  return (
    <ModalRoot onCancel={closeModal}>
      <TrackLine track={track} />
      <Focusable style={{ ...s.stack, marginTop: 14 }} flow-children="vertical">
        <Btn icon={<FaPlay />} label="Play now" onClick={() => run(() => api.playTrack(track))} />
        <Btn icon={<FaStepForward />} label="Play next" onClick={() => run(() => api.playNext(track), "Plays next.")} />
        <Btn icon={<FaListUl />} label="Add to playlist…" onClick={() => { closeModal?.(); openAddToPlaylist(track); }} />
        {downloaded === false && (
          <Btn icon={<FaDownload />} label="Download for offline" onClick={() => run(() => api.downloadTracks([track]), "Downloading…")} />
        )}
        {downloaded === true && (
          <Btn icon={<FaTrash />} label="Remove download" onClick={() => run(() => api.removeDownloads([track.id]), "Download removed.")} />
        )}
      </Focusable>
    </ModalRoot>
  );
}

// --- add to playlist / new playlist ----------------------------------------------------------

export function openAddToPlaylist(track: Track) {
  showModal(<AddToPlaylist track={track} />);
}

function AddToPlaylist({ track, closeModal }: { track: Track } & ModalProps) {
  const [playlists, setPlaylists] = useState<PlaylistSummary[] | null>(null);
  const [name, setName] = useState("");
  useLibraryReload(() => { api.getLibrary().then(l => setPlaylists(l.playlists)).catch(() => {}); });

  const add = async (playlist: PlaylistSummary) => {
    const result = await api.addToPlaylist(playlist.id, track);
    toast(!result.ok ? result.error : result.added ? `Added to “${playlist.name}”.` : `Already in “${playlist.name}”.`);
    closeModal?.();
  };

  const create = async () => {
    const result = await api.createPlaylist(name, track);
    toast(result.ok ? `Created “${name.trim()}” with this song.` : result.error);
    if (result.ok) closeModal?.();
  };

  return (
    <ModalRoot onCancel={closeModal}>
      <div style={s.heading}>Add to playlist</div>
      <div style={{ ...s.note, marginBottom: 10 }}>{track.title} · {artistNames(track)}</div>
      <div style={s.list}>
        {playlists?.map(p => (
          <Row key={p.id} onActivate={() => add(p)}>
            <div style={s.line}>
              <div style={s.text}><div style={s.title}>{p.name}</div><div style={s.subtitle}>{p.count} songs</div></div>
            </div>
          </Row>
        ))}
        {playlists?.length === 0 && <div style={s.note}>No playlists yet. Create your first one below.</div>}
      </div>
      <div style={{ marginTop: 12 }}>
        <TextField placeholder="New playlist name" value={name} onChange={e => setName(e.target.value)} />
      </div>
      <Focusable style={s.buttons} flow-children="horizontal">
        <Btn icon={<FaPlus />} label="Create and add" onClick={create} disabled={!name.trim()} />
      </Focusable>
    </ModalRoot>
  );
}

export function openNewPlaylist() {
  showModal(<NewPlaylist />);
}

function NewPlaylist({ closeModal }: ModalProps) {
  const [name, setName] = useState("");
  const create = async () => {
    const result = await api.createPlaylist(name, null);
    toast(result.ok ? `Created “${name.trim()}”. Add songs with X (or tap a cover) in search results.` : result.error);
    if (result.ok) closeModal?.();
  };
  return (
    <ModalRoot onCancel={closeModal}>
      <div style={s.heading}>New playlist</div>
      <TextField placeholder="Playlist name" value={name} onChange={e => setName(e.target.value)} focusOnMount />
      <Focusable style={s.buttons} flow-children="horizontal">
        <Btn icon={<FaPlus />} label="Create" onClick={create} disabled={!name.trim()} />
      </Focusable>
    </ModalRoot>
  );
}

// --- a playlist -----------------------------------------------------------------------------

export function openPlaylist(playlistId: string) {
  showModal(<PlaylistPage playlistId={playlistId} />);
}

function PlaylistPage({ playlistId, closeModal }: { playlistId: string } & ModalProps) {
  const [playlist, setPlaylist] = useState<Playlist | null>(null);
  useLibraryReload(() => {
    api.getPlaylist(playlistId).then(r => { if (r.ok) setPlaylist(r.playlist); else closeModal?.(); }).catch(() => {});
  });
  if (!playlist) return <ModalRoot onCancel={closeModal}><div style={s.note}>Loading…</div></ModalRoot>;

  const offline = playlist.tracks.filter(t => t.downloaded).length;
  const total = playlist.tracks.length;

  const play = (start: number, shuffle = false) => {
    api.playList(playlist.tracks, start, shuffle);
    closeModal?.();
  };

  const confirmDelete = () => showModal(
    <ConfirmModal
      strTitle={`Delete “${playlist.name}”?`}
      strDescription="The playlist is removed. Downloaded songs stay in your downloads."
      strOKButtonText="Delete"
      onOK={async () => { await api.deletePlaylist(playlist.id); toast("Playlist deleted."); closeModal?.(); }}
    />
  );

  return (
    <ModalRoot onCancel={closeModal}>
      <div style={s.heading}>{playlist.name}</div>
      <div style={s.note}>{total} songs · {offline} available offline</div>
      <Focusable style={s.buttons} flow-children="horizontal">
        <Btn icon={<FaPlay />} label="Play" onClick={() => play(0)} disabled={!total} />
        <Btn icon={<FaRandom />} label="Shuffle" onClick={() => play(0, true)} disabled={!total} />
        {offline < total && (
          <Btn icon={<FaDownload />} label="Download all" onClick={async () => {
            const r = await api.downloadPlaylist(playlist.id);
            toast(r.ok ? `Downloading ${r.added} song${r.added === 1 ? "" : "s"}…` : r.error);
          }} />
        )}
        <Btn icon={<FaTrash />} label="Delete" onClick={confirmDelete} />
      </Focusable>
      <div style={s.list}>
        {playlist.tracks.map((track, i) => (
          <Row key={track.id} onActivate={() => play(i)}
            onSecondary={() => api.removeFromPlaylist(playlist.id, track.id)} secondaryLabel="Remove from playlist">
            <TrackLine track={track} badge={track.downloaded ? <FaCheck title="Offline" /> : undefined} />
          </Row>
        ))}
        {!total && <div style={s.note}>Empty. Add songs from search: press X on a song (or tap its cover).</div>}
      </div>
    </ModalRoot>
  );
}

// --- downloads ------------------------------------------------------------------------------

export function openDownloads() {
  showModal(<Downloads />);
}

function Downloads({ closeModal }: ModalProps) {
  const [tracks, setTracks] = useState<Track[] | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  useLibraryReload(() => { api.getDownloads().then(d => { setTracks(d.tracks); setUsage(d.usage); }).catch(() => {}); });

  const play = (start: number, shuffle = false) => {
    if (tracks?.length) api.playList(tracks, start, shuffle);
    closeModal?.();
  };

  return (
    <ModalRoot onCancel={closeModal}>
      <div style={s.heading}>Downloads</div>
      <div style={s.note}>{usage ? `${usage.songs} songs · ${formatBytes(usage.bytes)} · play without internet` : "…"}</div>
      <Focusable style={s.buttons} flow-children="horizontal">
        <Btn icon={<FaPlay />} label="Play all" onClick={() => play(0)} disabled={!tracks?.length} />
        <Btn icon={<FaRandom />} label="Shuffle" onClick={() => play(0, true)} disabled={!tracks?.length} />
      </Focusable>
      <div style={s.list}>
        {tracks?.map((track, i) => (
          <Row key={track.id} onActivate={() => play(i)}
            onSecondary={() => api.removeDownloads([track.id])} secondaryLabel="Remove download">
            <TrackLine track={track} badge={track.size ? formatBytes(track.size) : undefined} />
          </Row>
        ))}
        {tracks?.length === 0 && <div style={s.note}>Nothing downloaded yet. Press X on a song (or tap its cover) and choose Download.</div>}
      </div>
    </ModalRoot>
  );
}

// --- debloat review ---------------------------------------------------------------------------

export function openStaleReview() {
  showModal(<StaleReview />);
}

function daysAgo(lastPlayed: number | null | undefined, added: number | undefined) {
  const days = (t: number) => Math.max(0, Math.floor((Date.now() / 1000 - t) / 86400));
  if (!lastPlayed) return added ? `never played · added ${days(added)}d ago` : "never played";
  const d = days(lastPlayed);
  return d === 0 ? "played today" : `last played ${d}d ago`;
}

function StaleReview({ closeModal }: ModalProps) {
  const [stale, setStale] = useState<Track[] | null>(null);
  useLibraryReload(() => { api.getDownloads().then(d => setStale(d.stale)).catch(() => {}); });

  const removeAll = () => showModal(
    <ConfirmModal
      strTitle={`Remove ${stale?.length} downloads?`}
      strDescription="They stay in your playlists and can be streamed or downloaded again any time."
      strOKButtonText="Remove"
      onOK={async () => {
        await api.removeDownloads((stale || []).map(t => t.id));
        toast("Downloads removed. Space freed.");
        closeModal?.();
      }}
    />
  );

  return (
    <ModalRoot onCancel={closeModal}>
      <div style={s.heading}>Haven't played in a while</div>
      <div style={{ ...s.note, marginBottom: 6 }}>Downloads you haven't listened to for 30 days or more. Removing them frees space; they stay in your playlists.</div>
      <Focusable style={s.buttons} flow-children="horizontal">
        <Btn icon={<FaTrash />} label={`Remove all ${stale?.length ?? ""}`} onClick={removeAll} disabled={!stale?.length} />
      </Focusable>
      <div style={s.list}>
        {stale?.map(track => (
          <Row key={track.id} onActivate={() => api.playTrack(track).then(() => closeModal?.())}
            onSecondary={() => api.removeDownloads([track.id])} secondaryLabel="Remove download">
            <TrackLine track={track} badge={<span style={{ fontSize: 11 }}>{daysAgo(track.lastPlayed, track.added)}</span>} />
          </Row>
        ))}
        {stale?.length === 0 && <div style={s.note}>Nothing to tidy up. Everything you've downloaded gets played.</div>}
      </div>
    </ModalRoot>
  );
}

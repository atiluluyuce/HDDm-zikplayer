// Sunucu iletişimi: REST çağrıları + SSE durum akışı.

const json = async (response) => {
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
};

const get = (path, params) => {
  const url = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
  });
  return fetch(url).then(json);
};

const post = (path, body) =>
  fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).then(json);

export const api = {
  state: () => get('/api/state'),
  home: () => get('/api/home'),
  albums: (offset = 0, limit = 60, sort = 'artist') => get('/api/albums', { offset, limit, sort }),
  album: (id) => get(`/api/albums/${id}`),
  artists: (offset = 0, limit = 200) => get('/api/artists', { offset, limit }),
  artist: (id) => get(`/api/artists/${id}`),
  search: (q) => get('/api/search', { q }),
  queue: () => get('/api/queue'),
  stats: () => get('/api/stats'),
  scanStatus: () => get('/api/scan'),

  playAlbum: (id, options) => post(`/api/play/album/${id}`, options),
  playArtist: (id, options) => post(`/api/play/artist/${id}`, options),
  playTrack: (id, options) => post(`/api/play/track/${id}`, options),
  playAlbumFrom: (albumId, trackId) => post(`/api/play/album/${albumId}`, { fromTrackId: trackId }),
  playMix: (mixId) => post(`/api/play/mix/${encodeURIComponent(mixId)}`),
  playRadio: (trackId) => post(`/api/play/radio/${trackId}`),
  shuffleAll: () => post('/api/play/shuffle'),

  toggle: () => post('/api/player/toggle'),
  next: () => post('/api/player/next'),
  previous: () => post('/api/player/previous'),
  seek: (seconds) => post('/api/player/seek', { seconds }),
  volume: (value) => post('/api/player/volume', { value }),
  option: (name, value) => post('/api/player/option', { name, value }),
  autoQueue: (value) => post('/api/player/autoqueue', { value }),

  queueAdd: (trackIds, options) => post('/api/queue/add', { trackIds, ...(options || {}) }),
  queueClear: () => post('/api/queue/clear'),
  queueJump: (position) => post('/api/queue/jump', { position }),
  queueRemove: (songId) => post('/api/queue/remove', { songId }),

  rate: (trackId, rating) => post(`/api/tracks/${trackId}/rating`, { rating }),
  favourite: () => post('/api/favourite'),

  scan: (full = false) => post('/api/scan', { full }),
  system: (action) => post(`/api/system/${action}`),
};

export const artUrl = (key, thumb = true) =>
  key ? `/api/art/${key}${thumb ? '?s=t' : ''}` : null;

/** Durum akışını dinler; bağlantı koparsa tarayıcı EventSource'u kendi yeniden kurar. */
export function watchState(onState, onConnection) {
  let source;

  const connect = () => {
    source = new EventSource('/api/events');
    source.onopen = () => onConnection?.(true);
    source.onmessage = (event) => {
      try {
        onState(JSON.parse(event.data));
      } catch { /* bozuk kare atlanır */ }
    };
    source.onerror = () => {
      onConnection?.(false);
      // EventSource kendiliğinden yeniden bağlanır; kapanmışsa elle kur.
      if (source.readyState === EventSource.CLOSED) setTimeout(connect, 2000);
    };
  };

  connect();
  return () => source?.close();
}

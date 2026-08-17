// hddmusicplayer web arayüzü — hash tabanlı basit yönlendirici + görünümler.

import { api, artUrl, watchState } from './api.js';

const view = document.getElementById('view');
const $ = (id) => document.getElementById(id);

let state = {};
let stateAt = 0;
let seeking = false;

// --- Yardımcılar -------------------------------------------------------------

const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => {
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
  });
  (Array.isArray(children) ? children : [children])
    .filter(Boolean)
    .forEach((child) => node.append(child));
  return node;
};

const time = (seconds) => {
  if (!seconds || seconds < 0) return '0:00';
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
           : `${m}:${String(s).padStart(2, '0')}`;
};

const duration = (seconds) => {
  const minutes = Math.round((seconds || 0) / 60);
  if (minutes < 60) return `${minutes} dk`;
  return `${Math.floor(minutes / 60)} sa ${minutes % 60} dk`;
};

let toastTimer;
const toast = (message) => {
  const node = $('toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 2200);
};

const artNode = (key, className = '') => {
  const url = artUrl(key);
  if (!url) return el('div', { class: `art placeholder ${className}` });
  return el('div', { class: `art ${className}` }, el('img', { src: url, loading: 'lazy', alt: '' }));
};

// --- Kartlar ve satırlar -----------------------------------------------------

const albumCard = (album) =>
  el('button', {
    class: 'card',
    onclick: () => { location.hash = `#/album/${album.id}`; },
  }, [
    artNode(album.art),
    el('div', { class: 'name', text: album.name }),
    el('div', { class: 'sub', text: album.artist || '' }),
  ]);

const artistCard = (artist) =>
  el('button', {
    class: 'card round',
    onclick: () => { location.hash = `#/artist/${artist.id}`; },
  }, [
    artNode(artist.art),
    el('div', { class: 'name', text: artist.name }),
    el('div', { class: 'sub', text: `${artist.albumCount} albüm` }),
  ]);

const mixCard = (mix) =>
  el('button', {
    class: 'card mix',
    onclick: async () => { await api.playMix(mix.id); toast(`${mix.title} çalınıyor`); },
  }, [
    el('div', { class: `art ${mix.art ? '' : 'placeholder'}` }, [
      mix.art ? el('img', { src: artUrl(mix.art), loading: 'lazy', alt: '' }) : null,
      el('div', { class: 'label', text: mix.title }),
    ]),
    el('div', { class: 'sub', text: mix.subtitle || '' }),
  ]);

/** Parça satırı. `context` çalma davranışını belirler. */
const trackRow = (track, options = {}) => {
  const { index, showArt, onPlay, current } = options;
  const loved = (track.rating || 0) > 0;

  const heart = el('button', {
    class: `heart ${loved ? 'on' : ''}`,
    title: loved ? 'Favorilerden çıkar' : 'Favorilere ekle',
    onclick: async (event) => {
      event.stopPropagation();
      const next = loved ? 0 : 1;
      await api.rate(track.id, next);
      heart.classList.toggle('on', next > 0);
      track.rating = next;
    },
  }, document.createTextNode(loved ? '♥' : '♡'));

  return el('div', {
    class: `row ${current ? 'current' : ''}`,
    onclick: () => onPlay?.(track),
  }, [
    showArt ? el('img', { src: artUrl(track.art) || '', alt: '', loading: 'lazy' })
            : el('div', { class: 'index', text: index !== undefined ? String(index) : '' }),
    el('div', { class: 'row-main' }, [
      el('div', { class: 'row-title', text: track.title || '—' }),
      el('div', { class: 'row-sub', text: [track.artist, track.album].filter(Boolean).join(' — ') }),
    ]),
    track.id ? heart : null,
    el('div', { class: 'dur', text: time(track.duration) }),
  ]);
};

const section = (title, body) => [el('h2', { text: title }), body];
const emptyState = (message) => el('div', { class: 'empty', text: message });

// --- Görünümler --------------------------------------------------------------

async function viewHome() {
  const data = await api.home();
  view.replaceChildren();

  if (data.mixes?.length) {
    view.append(...section('Senin İçin', el('div', { class: 'rail' }, data.mixes.map(mixCard))));
  }
  if (data.recentlyAdded?.length) {
    view.append(...section('Yeni Eklenenler',
      el('div', { class: 'rail' }, data.recentlyAdded.map(albumCard))));
  }
  if (data.recentlyPlayed?.length) {
    view.append(...section('Son Çalınanlar', el('div', { class: 'list' },
      data.recentlyPlayed.slice(0, 15).map((track) =>
        trackRow(track, { showArt: true, onPlay: (t) => api.playRadio(t.id) })))));
  }
  if (!view.children.length) {
    view.append(emptyState('Kütüphane boş görünüyor — Ayarlar\'dan tarama başlat.'));
  }
}

async function viewAlbums() {
  const sort = sessionStorage.getItem('albumSort') || 'artist';
  view.replaceChildren();

  const select = el('select', {
    class: 'ghost',
    onchange: (event) => {
      sessionStorage.setItem('albumSort', event.target.value);
      viewAlbums();
    },
  }, [
    ['artist', 'Sanatçıya göre'], ['recent', 'Yeni eklenen'],
    ['name', 'İsme göre'], ['year', 'Yıla göre'], ['random', 'Rastgele'],
  ].map(([value, label]) => el('option', { value, selected: value === sort ? '' : null },
    document.createTextNode(label))));

  const header = el('div', {
    style: 'display:flex;align-items:center;justify-content:space-between;gap:12px;margin:4px 0 14px',
  }, [el('h2', { text: 'Albümler', style: 'margin:0' }), select]);
  view.append(header);

  const grid = el('div', { class: 'grid' });
  view.append(grid);

  let offset = 0;
  let total = Infinity;
  let loading = false;

  const loadMore = async () => {
    if (loading || offset >= total) return;
    loading = true;
    const page = await api.albums(offset, 60, sort);
    total = page.total;
    grid.append(...page.items.map(albumCard));
    offset += page.items.length;
    loading = false;
    if (!page.items.length) total = offset;
  };

  await loadMore();
  if (!grid.children.length) {
    view.append(emptyState('Hiç albüm yok.'));
    return;
  }

  // Sayfa sonuna yaklaşınca sonraki grubu getir.
  const sentinel = el('div', { style: 'height:1px' });
  view.append(sentinel);
  new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) loadMore();
  }, { rootMargin: '600px' }).observe(sentinel);
}

async function viewArtists() {
  const data = await api.artists(0, 500);
  view.replaceChildren(el('h2', { text: `Sanatçılar (${data.total})` }));
  view.append(data.items.length
    ? el('div', { class: 'grid' }, data.items.map(artistCard))
    : emptyState('Hiç sanatçı yok.'));
}

async function viewAlbum(id) {
  const album = await api.album(id);
  if (!album) { view.replaceChildren(emptyState('Albüm bulunamadı.')); return; }

  const art = artUrl(album.art, false);
  const head = el('div', { class: 'detail-head' }, [
    art ? el('img', { src: art, alt: '' }) : el('div', { class: 'ph' }),
    el('div', { class: 'info' }, [
      el('h1', { text: album.name }),
      el('div', {
        class: 'by',
        text: album.artist || 'Bilinmeyen sanatçı',
        style: 'cursor:pointer',
        onclick: () => { if (album.artistId) location.hash = `#/artist/${album.artistId}`; },
      }),
      el('div', { class: 'muted', text: [album.year, `${album.trackCount} parça`,
        duration(album.duration)].filter(Boolean).join(' · ') }),
      el('div', { class: 'actions' }, [
        el('button', {
          class: 'btn',
          text: 'Çal',
          onclick: async () => { await api.playAlbum(id); toast('Albüm çalınıyor'); },
        }),
        el('button', {
          class: 'btn sec',
          text: 'Kuyruğa ekle',
          onclick: async () => {
            await api.queueAdd(album.tracks.map((t) => t.id));
            toast('Kuyruğa eklendi');
          },
        }),
      ]),
    ]),
  ]);

  const list = el('div', { class: 'list' }, album.tracks.map((track, index) =>
    trackRow(track, {
      index: track.trackNo || index + 1,
      current: state.track?.id === track.id,
      onPlay: (t) => api.playAlbumFrom(id, t.id),
    })));

  view.replaceChildren(head, list);
}

async function viewArtist(id) {
  const artist = await api.artist(id);
  if (!artist) { view.replaceChildren(emptyState('Sanatçı bulunamadı.')); return; }

  const art = artUrl(artist.art, false);
  const head = el('div', { class: 'detail-head' }, [
    art ? el('img', { src: art, alt: '' }) : el('div', { class: 'ph' }),
    el('div', { class: 'info' }, [
      el('h1', { text: artist.name }),
      el('div', { class: 'muted',
        text: `${artist.albumCount} albüm · ${artist.trackCount} parça` }),
      el('div', { class: 'actions' }, [
        el('button', {
          class: 'btn',
          text: 'Karıştırarak çal',
          onclick: async () => { await api.playArtist(id, { shuffle: true }); toast('Çalınıyor'); },
        }),
        el('button', {
          class: 'btn sec',
          text: 'Sırayla çal',
          onclick: async () => { await api.playArtist(id); toast('Çalınıyor'); },
        }),
      ]),
    ]),
  ]);

  view.replaceChildren(head, el('h2', { text: 'Albümler' }),
    el('div', { class: 'grid' }, artist.albums.map(albumCard)));
}

async function viewQueue() {
  const data = await api.queue();
  const items = data.items || [];

  const header = el('div', {
    style: 'display:flex;align-items:center;justify-content:space-between;margin:4px 0 14px',
  }, [
    el('h2', { text: `Kuyruk (${items.length})`, style: 'margin:0' }),
    items.length ? el('button', {
      class: 'ghost',
      text: 'Temizle',
      onclick: async () => { await api.queueClear(); viewQueue(); },
    }) : null,
  ]);

  view.replaceChildren(header);
  view.append(items.length
    ? el('div', { class: 'list' }, items.map((track) =>
        trackRow(track, {
          index: track.position + 1,
          current: track.position === state.queuePosition,
          onPlay: (t) => api.queueJump(t.position),
        })))
    : emptyState('Kuyruk boş. Ana sayfadan bir mix seç ya da "Karıştır"a bas.'));
}

async function viewSearch(query) {
  if (!query) { view.replaceChildren(emptyState('Arama yapmak için yaz.')); return; }
  const results = await api.search(query);
  view.replaceChildren();

  if (results.artists?.length) {
    view.append(...section('Sanatçılar',
      el('div', { class: 'grid' }, results.artists.map(artistCard))));
  }
  if (results.albums?.length) {
    view.append(...section('Albümler', el('div', { class: 'grid' }, results.albums.map(albumCard))));
  }
  if (results.tracks?.length) {
    view.append(...section('Parçalar', el('div', { class: 'list' }, results.tracks.map((track) =>
      trackRow(track, { showArt: true, onPlay: (t) => api.playTrack(t.id, { replace: true }) })))));
  }
  if (!view.children.length) view.append(emptyState(`"${query}" için sonuç yok.`));
}

async function viewSettings() {
  const [stats, scan] = await Promise.all([api.stats(), api.scanStatus()]);
  const options = state.options || {};

  const toggleRow = (label, description, value, onChange) =>
    el('div', { class: 'setting' }, [
      el('div', {}, [el('div', { class: 'label', text: label }),
                     el('div', { class: 'desc', text: description })]),
      el('button', {
        class: `chip ${value ? 'on' : ''}`,
        text: value ? 'Açık' : 'Kapalı',
        onclick: async () => { await onChange(!value); viewSettings(); },
      }),
    ]);

  const scanProgress = el('span', { style: `width:${Math.round((scan.progress || 0) * 100)}%` });

  view.replaceChildren(
    el('h2', { text: 'Kütüphane' }),
    el('div', { class: 'stats' }, [
      ['Parça', stats.tracks], ['Albüm', stats.albums], ['Sanatçı', stats.artists],
      ['Favori', stats.favourites], ['Hiç çalınmamış', stats.unplayed], ['Çalma', stats.plays],
    ].map(([key, n]) => el('div', { class: 'stat' }, [
      el('div', { class: 'n', text: String(n ?? 0) }),
      el('div', { class: 'k', text: key }),
    ]))),

    el('div', { class: 'setting', style: 'margin-top:14px' }, [
      el('div', { style: 'flex:1' }, [
        el('div', { class: 'label', text: 'Kütüphane taraması' }),
        el('div', { class: 'desc',
          text: scan.running ? `${scan.phase} — ${scan.processed}/${scan.total}`
                             : `${duration(stats.duration)} müzik indekslendi` }),
        scan.running ? el('div', { class: 'scanbar' }, scanProgress) : null,
      ]),
      el('button', {
        class: 'btn sec',
        text: scan.running ? 'Sürüyor…' : 'Tara',
        onclick: async () => { await api.scan(false); toast('Tarama başladı'); viewSettings(); },
      }),
    ]),

    el('h2', { text: 'Çalma' }),
    toggleRow('Otomatik kuyruk', 'Kuyruk bitmeden öneri motoru yeni parça ekler',
      state.autoQueue, (value) => api.autoQueue(value)),
    toggleRow('Karıştır', 'MPD sırayı rastgele çalar',
      options.random, (value) => api.option('random', value)),
    toggleRow('Tekrarla', 'Kuyruk bitince baştan başlar',
      options.repeat, (value) => api.option('repeat', value)),

    el('h2', { text: 'Sistem' }),
    el('div', { class: 'setting' }, [
      el('div', {}, [
        el('div', { class: 'label', text: 'Ses çıkışı' }),
        el('div', { class: 'desc', text: state.volumeMode === 'fixed'
          ? 'Sabit çıkış — seviye amfiden ayarlanıyor (bit-perfect)'
          : 'Yazılım seviyesi (ALSA softvol)' }),
      ]),
    ]),
    el('div', { class: 'setting' }, [
      el('div', {}, [
        el('div', { class: 'label', text: 'Güç' }),
        el('div', { class: 'desc', text: 'Kapatmadan önce diskin senkronize olmasını bekler' }),
      ]),
      el('div', { style: 'display:flex;gap:8px' }, [
        el('button', {
          class: 'chip',
          text: 'Yeniden başlat',
          onclick: () => { if (confirm('Yeniden başlatılsın mı?')) api.system('reboot'); },
        }),
        el('button', {
          class: 'chip',
          text: 'Kapat',
          onclick: () => { if (confirm('Cihaz kapatılsın mı?')) api.system('poweroff'); },
        }),
      ]),
    ]),
  );
}

// --- Çalan parça alt sayfası -------------------------------------------------

// Alt sayfanın tamamı her saniye yeniden kurulursa kaydırıcı sürüklenemez ve
// odak kaybolur. Bu yüzden ağır kurulum yalnızca parça değişince yapılır;
// aradaki güncellemelerde sadece konum bilgisi tazelenir.
let sheetTrackId = null;
let sheetParts = null;

function openSheet() {
  const track = state.track;
  if (!track) { toast('Çalan parça yok'); return; }
  $('sheet').hidden = false;
  sheetTrackId = null;
  renderSheet();
}

function renderSheet() {
  if ($('sheet').hidden) return;
  const track = state.track;
  const body = $('sheet-body');
  if (!track) {
    body.replaceChildren(emptyState('Çalan parça yok'));
    sheetTrackId = null;
    sheetParts = null;
    return;
  }

  if (track.id === sheetTrackId && sheetParts) {
    updateSheet();
    return;
  }

  const art = artUrl(track.art, false);
  const playing = state.state === 'play';
  const loved = (track.rating || 0) > 0;
  const elapsed = interpolatedElapsed();
  const total = state.duration || track.duration || 0;

  const slider = el('input', {
    type: 'range', min: 0, max: Math.max(1, Math.floor(total)), value: Math.floor(elapsed),
    oninput: () => { seeking = true; },
    onchange: (event) => { seeking = false; api.seek(Number(event.target.value)); },
  });
  const elapsedLabel = el('span', { text: time(elapsed) });
  const totalLabel = el('span', { text: time(total) });
  const toggleButton = el('button', {
    class: 'primary', html: playing ? '❚❚' : '▶', onclick: () => api.toggle(),
  });
  const favouriteButton = el('button', {
    class: `chip ${loved ? 'on' : ''}`,
    text: loved ? '♥ Favori' : '♡ Favori',
    onclick: async () => { await api.favourite(); },
  });

  sheetTrackId = track.id;
  sheetParts = { slider, elapsedLabel, totalLabel, toggleButton, favouriteButton };

  body.replaceChildren(
    el('div', { class: 'now-big' }, [
      art ? el('img', { src: art, alt: '' }) : el('div', { class: 'ph' }),
      el('h1', { text: track.title || '—' }),
      el('div', {
        class: 'by', text: track.artist || '',
        style: 'cursor:pointer',
        onclick: () => {
          if (!track.artistId) return;
          $('sheet').hidden = true;
          location.hash = `#/artist/${track.artistId}`;
        },
      }),
      el('div', {
        class: 'muted', style: 'cursor:pointer;font-size:13px',
        text: track.album || '',
        onclick: () => {
          if (!track.albumId) return;
          $('sheet').hidden = true;
          location.hash = `#/album/${track.albumId}`;
        },
      }),

      el('div', { class: 'seek' }, slider),
      el('div', { class: 'times' }, [elapsedLabel, totalLabel]),

      el('div', { class: 'big-controls' }, [
        el('button', { html: '⏮', onclick: () => api.previous() }),
        toggleButton,
        el('button', { html: '⏭', onclick: () => api.next() }),
      ]),

      el('div', { class: 'extra-controls' }, [
        favouriteButton,
        el('button', {
          class: 'chip',
          text: '◉ Benzerlerini çal',
          onclick: async () => { await api.playRadio(track.id); toast('Benzerler sıraya alındı'); },
        }),
        el('button', {
          class: 'chip',
          text: '✕ Bir daha çalma',
          onclick: async () => { await api.rate(track.id, -1); await api.next(); toast('Yasaklandı'); },
        }),
      ]),

      state.volumeMode === 'fixed'
        ? el('div', { class: 'meta', text: 'Sabit çıkış — sesi amfiden ayarla' })
        : el('div', { class: 'volume' }, [
            el('span', { html: '🔈' }),
            el('input', {
              type: 'range', min: 0, max: 100, value: state.volume || 0,
              onchange: (event) => api.volume(Number(event.target.value)),
            }),
            el('span', { text: `${state.volume || 0}` }),
          ]),

      el('div', { class: 'meta',
        text: [track.year, track.genre, track.bitrate ? `${track.bitrate} kbps` : null,
               state.audio].filter(Boolean).join(' · ') }),
    ]),
  );
}

/** Alt sayfada yalnızca zamanla değişen parçaları tazeler. */
function updateSheet() {
  const { slider, elapsedLabel, totalLabel, toggleButton, favouriteButton } = sheetParts;
  const elapsed = interpolatedElapsed();
  const total = state.duration || state.track?.duration || 0;

  if (!seeking) {
    slider.max = String(Math.max(1, Math.floor(total)));
    slider.value = String(Math.floor(elapsed));
  }
  elapsedLabel.textContent = time(elapsed);
  totalLabel.textContent = time(total);
  toggleButton.innerHTML = state.state === 'play' ? '❚❚' : '▶';

  const loved = (state.track?.rating || 0) > 0;
  favouriteButton.className = `chip ${loved ? 'on' : ''}`;
  favouriteButton.textContent = loved ? '♥ Favori' : '♡ Favori';
}

// --- Çalar çubuğu ------------------------------------------------------------

function interpolatedElapsed() {
  const base = state.elapsed || 0;
  if (state.state !== 'play') return base;
  return base + Math.max(0, (Date.now() - stateAt) / 1000);
}

function renderPlayer() {
  const player = $('player');
  const track = state.track;
  player.hidden = !track && state.state === 'stop' && !state.queueLength;

  $('np-title').textContent = track?.title || 'Çalan parça yok';
  $('np-artist').textContent = track?.artist || '';
  $('np-toggle').innerHTML = state.state === 'play' ? '❚❚' : '▶';

  const image = $('np-art');
  const url = artUrl(track?.art);
  if (url) { image.src = url; image.style.visibility = 'visible'; }
  else { image.removeAttribute('src'); image.style.visibility = 'hidden'; }

  const total = state.duration || 0;
  const ratio = total > 0 ? Math.min(1, interpolatedElapsed() / total) : 0;
  $('np-progress').style.width = `${ratio * 100}%`;
}

// --- Yönlendirici ------------------------------------------------------------

const routes = [
  [/^#\/home$/, viewHome],
  [/^#\/albums$/, viewAlbums],
  [/^#\/artists$/, viewArtists],
  [/^#\/queue$/, viewQueue],
  [/^#\/settings$/, viewSettings],
  [/^#\/album\/(\d+)$/, viewAlbum],
  [/^#\/artist\/(\d+)$/, viewArtist],
  [/^#\/search\/(.*)$/, (q) => viewSearch(decodeURIComponent(q))],
];

async function route() {
  const hash = location.hash || '#/home';
  const tab = hash.split('/')[1];
  document.querySelectorAll('#tabs a').forEach((link) => {
    link.classList.toggle('active', link.dataset.tab === tab);
  });

  for (const [pattern, handler] of routes) {
    const match = hash.match(pattern);
    if (match) {
      view.replaceChildren(el('div', { class: 'empty', text: 'yükleniyor…' }));
      try {
        await handler(...match.slice(1));
      } catch (error) {
        view.replaceChildren(emptyState(`Yüklenemedi: ${error.message}`));
      }
      window.scrollTo(0, 0);
      return;
    }
  }
  location.hash = '#/home';
}

// --- Başlangıç ---------------------------------------------------------------

function bind() {
  $('np-toggle').onclick = () => api.toggle();
  $('np-next').onclick = () => api.next();
  $('np-prev').onclick = () => api.previous();
  $('np-art-button').onclick = openSheet;
  $('np-meta').onclick = openSheet;
  $('sheet-close').onclick = () => { $('sheet').hidden = true; };
  $('sheet').onclick = (event) => {
    if (event.target === $('sheet')) $('sheet').hidden = true;
  };
  $('shuffle-all').onclick = async () => {
    await api.shuffleAll();
    toast('Akıllı karıştırma başladı');
  };

  let searchTimer;
  $('search-input').oninput = (event) => {
    clearTimeout(searchTimer);
    const query = event.target.value.trim();
    searchTimer = setTimeout(() => {
      location.hash = query ? `#/search/${encodeURIComponent(query)}` : '#/home';
    }, 280);
  };
  $('search-form').onsubmit = (event) => event.preventDefault();

  window.addEventListener('hashchange', route);
  document.addEventListener('keydown', (event) => {
    if (event.target.tagName === 'INPUT') return;
    if (event.code === 'Space') { event.preventDefault(); api.toggle(); }
    if (event.code === 'ArrowRight' && event.shiftKey) api.next();
    if (event.code === 'ArrowLeft' && event.shiftKey) api.previous();
  });
}

function start() {
  bind();
  route();

  watchState(
    (next) => {
      const trackChanged = next.track?.id !== state.track?.id;
      state = next;
      stateAt = Date.now();
      renderPlayer();
      if (!seeking) renderSheet();
      // Kuyruk ekranı açıkken parça değişirse listeyi tazele.
      if (trackChanged && location.hash === '#/queue') viewQueue();
    },
    (online) => $('conn-dot').classList.toggle('online', online),
  );

  // İlerleme çubuğunu akıcı tutmak için yerel sayaç; sunucudan saniyede bir
  // gerçek değer geldiği için kayma birikmiyor.
  setInterval(() => {
    if (state.state === 'play') {
      renderPlayer();
      if (!seeking && !$('sheet').hidden) renderSheet();
    }
  }, 1000);
}

start();

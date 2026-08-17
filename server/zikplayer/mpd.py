"""Küçük, bağımlılıksız asenkron MPD istemcisi.

MPD protokolü satır tabanlı ve basit; harici kütüphane yerine burada tutmak
Raspberry Pi'de kurulum yükünü sıfırlıyor ve yeniden bağlanma davranışını
tamamen bizim kontrolümüze bırakıyor.

İki sınıf var:
  MPD       - komut göndermek için, otomatik yeniden bağlanan tekil bağlantı
  MPDWatcher- ayrı bir bağlantıda `idle` bekleyip değişiklikleri haber verir
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Iterable, Sequence

log = logging.getLogger(__name__)


class MPDError(Exception):
    """MPD ile ilgili tüm hataların atası."""


class MPDConnectionError(MPDError):
    """Bağlantı kurulamadı veya koptu."""


class MPDCommandError(MPDError):
    """Sunucu ACK döndürdü (geçersiz komut, bulunamayan dosya vb.)."""


Pairs = list[tuple[str, str]]


def _quote(arg: Any) -> str:
    """Argümanı MPD'nin beklediği biçimde tırnaklar."""
    s = str(arg)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format(command: str, *args: Any) -> str:
    if not args:
        return command
    return command + " " + " ".join(_quote(a) for a in args)


def group(pairs: Pairs, delimiter: str) -> list[dict[str, str]]:
    """Düz anahtar/değer akışını kayıtlara böler.

    MPD listeleri, her kaydın `delimiter` anahtarıyla başladığı tek bir akış
    olarak döner (ör. playlistinfo için "file").
    """
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for key, value in pairs:
        if key == delimiter or current is None:
            current = {}
            records.append(current)
        # Aynı anahtar tekrar ederse (çoklu sanatçı gibi) araya ", " koyarak birleştir.
        if key in current:
            current[key] = f"{current[key]}, {value}"
        else:
            current[key] = value
    return records


def to_dict(pairs: Pairs) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in pairs:
        out[key] = value
    return out


class _Connection:
    """Tek bir TCP bağlantısı ve protokol çerçeveleme."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.version = ""
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def open(self, timeout: float = 5.0) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout
            )
            banner = await self._readline()
        except (OSError, asyncio.TimeoutError) as exc:
            self._reader = self._writer = None
            raise MPDConnectionError(f"MPD'ye bağlanılamadı ({self.host}:{self.port}): {exc}") from exc

        if not banner.startswith("OK MPD "):
            await self.close()
            raise MPDConnectionError(f"beklenmeyen MPD karşılaması: {banner!r}")
        self.version = banner[7:].strip()

    async def close(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is None:
            return
        try:
            writer.close()
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            pass

    async def _readline(self) -> str:
        assert self._reader is not None
        try:
            raw = await self._reader.readline()
        except OSError as exc:
            raise MPDConnectionError(f"okuma hatası: {exc}") from exc
        if not raw:
            raise MPDConnectionError("MPD bağlantıyı kapattı")
        return raw.decode("utf-8", "replace").rstrip("\r\n")

    async def write(self, *lines: str) -> None:
        if self._writer is None:
            raise MPDConnectionError("bağlantı yok")
        payload = "".join(line + "\n" for line in lines).encode("utf-8")
        try:
            self._writer.write(payload)
            await self._writer.drain()
        except OSError as exc:
            raise MPDConnectionError(f"yazma hatası: {exc}") from exc

    async def read_pairs(self) -> Pairs:
        """OK/ACK görene kadar anahtar/değer satırlarını toplar."""
        pairs: Pairs = []
        while True:
            line = await self._readline()
            if line == "OK":
                return pairs
            if line.startswith("ACK "):
                raise MPDCommandError(line[4:].strip())
            if line.startswith("list_OK"):
                continue  # command_list_ok_begin kullanmıyoruz ama zararsız
            key, sep, value = line.partition(": ")
            if sep:
                pairs.append((key, value))


class MPD:
    """Komut arayüzü. Bağlantı koptuğunda bir kez sessizce yeniden dener."""

    def __init__(self, host: str, port: int) -> None:
        self._conn = _Connection(host, port)
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            await self._conn.close()

    async def _ensure(self) -> None:
        if not self._conn.connected:
            await self._conn.open()

    async def execute(self, command: str, *args: Any) -> Pairs:
        return await self._run([_format(command, *args)])

    async def command_list(self, commands: Sequence[tuple[str, ...]]) -> Pairs:
        """Birden çok komutu tek gidiş-dönüşte çalıştırır.

        Kuyruğa yüzlerce parça eklerken tek tek göndermek Pi'de saniyeler
        alıyor; toplu gönderim bunu milisaniyelere indiriyor.
        """
        if not commands:
            return []
        lines = ["command_list_begin"]
        lines += [_format(cmd[0], *cmd[1:]) for cmd in commands]
        lines.append("command_list_end")
        return await self._run(lines)

    async def _run(self, lines: list[str]) -> Pairs:
        async with self._lock:
            for attempt in (1, 2):
                try:
                    await self._ensure()
                    await self._conn.write(*lines)
                    return await self._conn.read_pairs()
                except MPDConnectionError:
                    await self._conn.close()
                    if attempt == 2:
                        raise
                    log.warning("MPD bağlantısı koptu, yeniden bağlanılıyor")
        return []

    # --- Sık kullanılan komutlar --------------------------------------------

    async def status(self) -> dict[str, str]:
        return to_dict(await self.execute("status"))

    async def stats(self) -> dict[str, str]:
        return to_dict(await self.execute("stats"))

    async def current_song(self) -> dict[str, str]:
        return to_dict(await self.execute("currentsong"))

    async def playlist(self) -> list[dict[str, str]]:
        return group(await self.execute("playlistinfo"), "file")

    async def play(self, position: int | None = None) -> None:
        if position is None:
            await self.execute("play")
        else:
            await self.execute("play", position)

    async def play_id(self, song_id: int) -> None:
        await self.execute("playid", song_id)

    async def pause(self, paused: bool | None = None) -> None:
        if paused is None:
            await self.execute("pause")
        else:
            await self.execute("pause", 1 if paused else 0)

    async def stop(self) -> None:
        await self.execute("stop")

    async def next(self) -> None:
        await self.execute("next")

    async def previous(self) -> None:
        await self.execute("previous")

    async def seek(self, seconds: float) -> None:
        await self.execute("seekcur", f"{max(0.0, seconds):.3f}")

    async def set_volume(self, volume: int) -> None:
        await self.execute("setvol", max(0, min(100, int(volume))))

    async def clear(self) -> None:
        await self.execute("clear")

    async def add(self, uris: Iterable[str]) -> None:
        await self.command_list([("add", uri) for uri in uris])

    async def add_ids(self, uris: Iterable[str]) -> list[int]:
        pairs = await self.command_list([("addid", uri) for uri in uris])
        return [int(v) for k, v in pairs if k == "Id"]

    async def delete_id(self, song_id: int) -> None:
        await self.execute("deleteid", song_id)

    async def move_id(self, song_id: int, position: int) -> None:
        await self.execute("moveid", song_id, position)

    async def set_option(self, name: str, value: bool | int) -> None:
        """random / repeat / single / consume."""
        await self.execute(name, int(value))

    async def update(self, uri: str | None = None) -> None:
        if uri:
            await self.execute("update", uri)
        else:
            await self.execute("update")

    async def ping(self) -> bool:
        try:
            await self.execute("ping")
            return True
        except MPDError:
            return False


class MPDWatcher:
    """`idle` üzerinde bekleyip değişen alt sistemleri bildirir.

    Ayrı bağlantı kullanır: `idle` bloklayıcı olduğu için komut bağlantısıyla
    paylaşılamaz.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_change: Callable[[set[str]], Awaitable[None]],
        subsystems: Sequence[str] = ("player", "playlist", "mixer", "options", "update"),
    ) -> None:
        self._conn = _Connection(host, port)
        self._on_change = on_change
        self._subsystems = list(subsystems)
        self._task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._loop(), name="mpd-watcher")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._conn.close()

    async def _loop(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                if not self._conn.connected:
                    await self._conn.open()
                    backoff = 1.0
                    # Bağlandığımız anda bir kez tetikle: bağlantı kopukken
                    # kaçırdığımız değişiklikler varsa durum tazelensin.
                    await self._on_change(set(self._subsystems))

                await self._conn.write(_format("idle", *self._subsystems))
                pairs = await self._conn.read_pairs()
                changed = {value for key, value in pairs if key == "changed"}
                if changed:
                    await self._on_change(changed)

            except asyncio.CancelledError:
                raise
            except MPDConnectionError as exc:
                await self._conn.close()
                if self._stopping:
                    return
                log.warning("MPD izleyici koptu (%s), %.0fsn sonra yeniden denenecek", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception:
                log.exception("MPD izleyicide beklenmeyen hata")
                await asyncio.sleep(2.0)

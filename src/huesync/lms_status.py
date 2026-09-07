"""Query the LMS CLI (port 9090) for a player's status.

The LMS CLI is a line-oriented TCP protocol.  The raw player MAC must be sent
as-is — URL-encoding the colons makes LMS silently fail to match the player.
Responses are URL-encoded; this module decodes them before parsing.

Standalone usage: python -m huesync.lms_status <host> <mac>
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass, field
from urllib.parse import unquote

DEFAULT_PORT: int = 9090
_SOCKET_TIMEOUT_S: float = 3.0

log = logging.getLogger(__name__)


@dataclass
class LmsPlayerStatus:
    """Parsed subset of the LMS CLI status response for one player."""

    time: float | None = None
    player_name: str | None = None      # display name of the queried player
    sync_master: str | None = None      # MAC of the sync-group master, None if standalone
    sync_slaves: list[str] = field(default_factory=list)   # MACs of sync slaves


def _recv_line(sock: socket.socket) -> bytes:
    """Read bytes from *sock* until a newline is received, accumulating chunks."""
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks)


def query_lms_status(host: str, mac: str, port: int = DEFAULT_PORT) -> LmsPlayerStatus:
    """Query a single player's status from the LMS CLI.

    *mac* is sent verbatim — LMS matches players by raw MAC and silently
    fails to find them if the colons are URL-encoded.  The response is
    fully URL-encoded by LMS, including the colon that separates keys from
    values (%3A).  See _parse_status for the parsing strategy.

    Raises ValueError if *host* is empty (misconfigured profile).
    Raises OSError / TimeoutError on connection or timeout errors.
    """
    if not host:
        raise ValueError(
            "LMS host is not configured in this profile. "
            "Open the profile editor and enter the LMS server IP or hostname."
        )
    command = f"{mac} status - 1 tags:\n"
    log.debug("LMS query: %s:%d player=%s", host, port, mac)
    with socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S) as sock:
        sock.sendall(command.encode("utf-8"))
        raw = _recv_line(sock).decode("utf-8", errors="replace").strip()
    log.debug("LMS raw response: %r", raw[:400])
    result = _parse_status(raw)
    log.debug("LMS parsed: player_name=%r sync_master=%r sync_slaves=%r",
              result.player_name, result.sync_master, result.sync_slaves)
    return result


def query_lms_sync_peers(host: str, mac: str, port: int = DEFAULT_PORT) -> list[str]:
    """Return the MAC addresses of all sync-group peers for *mac*.

    Uses the LMS 'sync ?' CLI command, which reflects the *configured* sync
    group regardless of play state.  The 'status' response only includes
    sync_master when the player is actively playing; this command is the
    reliable fallback for stopped/idle players.

    Returns an empty list if the player is standalone.
    Raises ValueError if *host* is empty (misconfigured profile).
    Raises OSError / TimeoutError on connection or timeout errors.
    """
    if not host:
        raise ValueError(
            "LMS host is not configured in this profile. "
            "Open the profile editor and enter the LMS server IP or hostname."
        )
    command = f"{mac} sync ?\n"
    log.debug("LMS sync? query: %s:%d player=%s", host, port, mac)
    with socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S) as sock:
        sock.sendall(command.encode("utf-8"))
        raw = _recv_line(sock).decode("utf-8", errors="replace").strip()
    log.debug("LMS sync? raw response: %r", raw[:200])
    return _parse_sync_response(raw)


def _parse_status(text: str) -> LmsPlayerStatus:
    """Parse the LMS CLI status response into an LmsPlayerStatus.

    LMS URL-encodes the full response, including the colon that separates
    key from value (arrives as %3A or occasionally %3a).  Splitting strategy:

      1. Split on literal spaces — these are never encoded, they delimit tokens.
      2. Normalise %3a → %3A so only one form needs to be matched.
      3. partition("%3A") to split key from value while both are still encoded —
         this means a decoded colon in a MAC value can never be mistaken for
         the structural separator.
      4. Unquote key and value independently.
    """
    result = LmsPlayerStatus()
    for token in text.split():
        key_raw, sep, value_raw = token.replace("%3a", "%3A").partition("%3A")
        if not sep:
            continue
        key = unquote(key_raw)
        value = unquote(value_raw)
        if key == "time":
            try:
                result.time = float(value)
            except ValueError:
                pass
        elif key == "player_name":
            result.player_name = value or None
        elif key == "sync_master":
            result.sync_master = value or None
        elif key == "sync_slaves":
            result.sync_slaves = [s for s in value.split(",") if s]
    return result


def _parse_sync_response(text: str) -> list[str]:
    """Parse the LMS 'sync ?' response into a list of peer MACs.

    Wire format: '<encoded_playerid> sync <peers_or_dash>'
    where <peers_or_dash> is '-' for standalone players or a comma-separated
    (URL-encoded as %2C) list of URL-encoded peer MACs.  Returns an empty
    list if the player is standalone or the response is malformed.
    """
    parts = text.split()
    if len(parts) < 3:
        return []
    peers_raw = parts[2]
    if peers_raw == "-":
        return []
    # Unquote first: %2C → comma, %3A → colon in MAC addresses.
    return [p for p in unquote(peers_raw).split(",") if p and p != "-"]


def unsync_player(host: str, mac: str, port: int = DEFAULT_PORT) -> None:
    """Remove *mac* from any LMS sync group.

    Safe to call when the player is already standalone — LMS silently
    accepts 'sync -' in that case.  Raises ValueError if *host* is empty.
    """
    if not host:
        raise ValueError("LMS host is not configured")
    command = f"{mac} sync -\n"
    log.debug("LMS unsync: %s:%d player=%s", host, port, mac)
    with socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S) as sock:
        sock.sendall(command.encode("utf-8"))
        _recv_line(sock)  # consume echoed response
    log.info("Removed LMS player %s from sync group (if any)", mac)


def list_lms_players(host: str, port: int = DEFAULT_PORT) -> list[dict]:
    """Return all LMS players as a list of dicts with 'playerid' and 'name'.

    Uses 'players 0 100 tags:' — returns up to 100 players.
    Raises ValueError if *host* is empty; raises OSError on connection failure.
    """
    if not host:
        raise ValueError("LMS host is not configured")
    log.debug("LMS list players: %s:%d", host, port)
    with socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S) as sock:
        sock.sendall(b"players 0 100 tags:\n")
        raw = _recv_line(sock).decode("utf-8", errors="replace").strip()
    return _parse_players(raw)


def _parse_players(text: str) -> list[dict]:
    """Parse the LMS 'players 0 N' CLI response into a list of player dicts.

    LMS URL-encodes the entire response.  Player fields are interleaved:
    each new 'playerid' key starts a new player record.
    """
    players: list[dict] = []
    current: dict | None = None
    for token in text.split():
        key_raw, sep, value_raw = token.replace("%3a", "%3A").partition("%3A")
        if not sep:
            continue
        key = unquote(key_raw)
        value = unquote(value_raw)
        if key == "playerid":
            if current is not None:
                players.append(current)
            current = {"playerid": value}
        elif current is not None:
            current[key] = value
    if current is not None:
        players.append(current)
    return players


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python -m huesync.lms_status <host> <mac>", file=sys.stderr)
        sys.exit(1)
    _host, _mac = sys.argv[1], sys.argv[2]
    _status = query_lms_status(_host, _mac)
    print(f"time:         {_status.time}")
    print(f"player_name:  {_status.player_name}")
    print(f"sync_master:  {_status.sync_master}")
    print(f"sync_slaves:  {_status.sync_slaves}")
    _peers = query_lms_sync_peers(_host, _mac)
    print(f"sync? peers:  {_peers}")

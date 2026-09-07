"""Tests for the JSON REST API (src/huesync/api.py).

Uses FastAPI's TestClient so no real network connections are made.
PlayerManager is mocked to avoid needing actual processes or bridges.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from fastapi.testclient import TestClient

from huesync.app import app
from huesync.models import (
    AnalysisConfig,
    Coupling,
    LightProvider,
    RenderConfig,
    VirtualPlayer,
)
from huesync.storage import Storage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "config.json")


def _make_mock_manager() -> MagicMock:
    manager = MagicMock()
    manager.detected_sync_master = None
    manager.latency_warning = None
    type(manager).applied_delay_ms = PropertyMock(return_value=0)
    type(manager).bridge_connected = PropertyMock(return_value=False)
    type(manager).process_status = PropertyMock(return_value={"squeezelite": False, "cava": False})
    type(manager).last_bars = PropertyMock(return_value=[])
    type(manager).active_coupling_id = PropertyMock(return_value=None)
    type(manager).active_coupling_name = PropertyMock(return_value=None)
    # Async methods
    manager.activate_coupling = AsyncMock()
    manager.deactivate = AsyncMock()
    manager.restart_cava = AsyncMock()
    manager.refresh_probe = AsyncMock()
    # Sync live-update methods
    manager.update_onset_pipeline = MagicMock()
    manager.update_render = MagicMock()
    return manager


@pytest.fixture()
def client(tmp_path: Path):
    storage = _make_storage(tmp_path)
    manager = _make_mock_manager()
    app.state.storage = storage
    app.state.player_manager = manager
    with TestClient(app) as c:
        c._manager = manager  # expose for test inspection
        c._storage = storage
        yield c


# ---------------------------------------------------------------------------
# Player latencies
# ---------------------------------------------------------------------------


def test_list_player_latencies_empty(client: TestClient):
    resp = client.get("/api/player-latencies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_player_latency_returns_201(client: TestClient):
    payload = {"player_mac": "AA:BB:CC:DD:EE:FF", "strategy": "fixed", "fixed_delay_ms": 1500}
    resp = client.post("/api/player-latencies", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    # MAC is stored lower-cased and stripped.
    assert body["player_mac"] == "aa:bb:cc:dd:ee:ff"
    assert body["fixed_delay_ms"] == 1500
    client._manager.refresh_probe.assert_called()


def test_patch_player_latency_404_for_unknown(client: TestClient):
    resp = client.patch("/api/player-latencies/00:11:22:33:44:55", json={"fixed_delay_ms": 999})
    assert resp.status_code == 404


def test_delete_player_latency_returns_204(client: TestClient):
    # Create an entry first.
    payload = {"player_mac": "11:22:33:44:55:66", "strategy": "fixed", "fixed_delay_ms": 2000}
    client.post("/api/player-latencies", json=payload)

    resp = client.delete("/api/player-latencies/11:22:33:44:55:66")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_get_status_returns_correct_shape(client: TestClient):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_coupling_id" in body
    assert "active_coupling_name" in body
    assert "sync_master" in body
    assert "applied_delay_ms" in body
    assert "latency_warning" in body
    assert "processes" in body
    assert "squeezelite" in body["processes"]
    assert "cava" in body["processes"]
    assert "bridge_connected" in body


# ---------------------------------------------------------------------------
# Controllers
# ---------------------------------------------------------------------------


def test_list_controllers_empty(client: TestClient):
    resp = client.get("/api/controllers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_and_get_controller(client: TestClient):
    payload = {"name": "Living Room Bridge", "host": "192.168.1.2", "type": "hue"}
    resp = client.post("/api/controllers", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Living Room Bridge"
    assert body["host"] == "192.168.1.2"
    assert body["type"] == "hue"
    controller_id = body["id"]

    resp2 = client.get(f"/api/controllers/{controller_id}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == controller_id


# ---------------------------------------------------------------------------
# VirtualPlayers
# ---------------------------------------------------------------------------


def test_create_and_list_virtual_players(client: TestClient):
    payload = {"name": "Main Player", "lms_host": "10.0.0.5"}
    resp = client.post("/api/virtual-players", json=payload)
    assert resp.status_code == 201

    resp2 = client.get("/api/virtual-players")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 1
    assert resp2.json()[0]["lms_host"] == "10.0.0.5"


def test_create_and_get_virtual_player(client: TestClient):
    payload = {"name": "Bedroom", "lms_host": "10.0.0.6", "lms_port": 9000}
    resp = client.post("/api/virtual-players", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    player_id = body["id"]
    # MAC should be auto-generated if not provided
    assert body["player_mac"] != ""

    resp2 = client.get(f"/api/virtual-players/{player_id}")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "Bedroom"


# ---------------------------------------------------------------------------
# LightProviders
# ---------------------------------------------------------------------------


def test_create_and_get_light_provider(client: TestClient):
    payload = {
        "name": "Living Room EA",
        "controller_id": "ctrl-1",
        "entertainment_area_id": "ea-1",
        "entertainment_area_name": "Living Room",
        "light_count": 4,
    }
    resp = client.post("/api/light-providers", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    lp_id = body["id"]
    assert body["name"] == "Living Room EA"
    assert body["light_count"] == 4

    resp2 = client.get(f"/api/light-providers/{lp_id}")
    assert resp2.status_code == 200
    assert resp2.json()["entertainment_area_name"] == "Living Room"


# ---------------------------------------------------------------------------
# AnalysisConfigs
# ---------------------------------------------------------------------------


def test_create_and_get_analysis_config(client: TestClient):
    payload = {"name": "Fast Onset", "onset_method": "superflux", "bars": 20}
    resp = client.post("/api/analysis-configs", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    ac_id = body["id"]
    assert body["onset_method"] == "superflux"
    assert body["bars"] == 20

    resp2 = client.get(f"/api/analysis-configs/{ac_id}")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "Fast Onset"


# ---------------------------------------------------------------------------
# RenderConfigs
# ---------------------------------------------------------------------------


def test_create_and_get_render_config(client: TestClient):
    payload = {"name": "Vivid", "effect": "spectrum_rgb", "sensitivity": 1.5}
    resp = client.post("/api/render-configs", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    rc_id = body["id"]
    assert body["sensitivity"] == 1.5

    resp2 = client.get(f"/api/render-configs/{rc_id}")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "Vivid"


# ---------------------------------------------------------------------------
# Couplings
# ---------------------------------------------------------------------------


def _make_full_coupling(storage: Storage) -> Coupling:
    """Create and persist all entities required for a Coupling, return the Coupling."""
    player = VirtualPlayer(name="P", lms_host="10.0.0.1", player_mac="aa:bb:cc:dd:ee:ff")
    lp = LightProvider(name="LP", controller_id="ctrl-1", entertainment_area_id="ea-1")
    ac = AnalysisConfig(name="AC")
    rc = RenderConfig(name="RC")
    storage.save_virtual_player(player)
    storage.save_light_provider(lp)
    storage.save_analysis_config(ac)
    storage.save_render_config(rc)
    coupling = Coupling(
        name="Test Coupling",
        player_id=player.id,
        light_provider_id=lp.id,
        analysis_config_id=ac.id,
        render_config_id=rc.id,
    )
    storage.save_coupling(coupling)
    return coupling


def test_create_coupling(client: TestClient):
    player = VirtualPlayer(name="P", lms_host="10.0.0.1")
    lp = LightProvider(name="LP", controller_id="c1", entertainment_area_id="ea-1")
    ac = AnalysisConfig(name="AC")
    rc = RenderConfig(name="RC")
    client._storage.save_virtual_player(player)
    client._storage.save_light_provider(lp)
    client._storage.save_analysis_config(ac)
    client._storage.save_render_config(rc)

    payload = {
        "name": "My Coupling",
        "player_id": player.id,
        "analysis_config_id": ac.id,
        "light_provider_id": lp.id,
        "render_config_id": rc.id,
    }
    resp = client.post("/api/couplings", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Coupling"
    assert body["player_id"] == player.id


def test_get_coupling_not_found(client: TestClient):
    resp = client.get("/api/couplings/no-such-id")
    assert resp.status_code == 404


def test_activate_coupling_missing_entities(client: TestClient):
    """Activating a coupling with missing FK entities must return 422."""
    coupling = Coupling(
        name="Broken",
        player_id="missing",
        light_provider_id="missing",
        analysis_config_id="missing",
        render_config_id="missing",
    )
    client._storage.save_coupling(coupling)
    client._manager.activate_coupling.side_effect = ValueError("missing Player")

    resp = client.post(f"/api/couplings/{coupling.id}/activate")
    assert resp.status_code == 422


def test_activate_coupling_success(client: TestClient):
    coupling = _make_full_coupling(client._storage)

    resp = client.post(f"/api/couplings/{coupling.id}/activate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_id"] == coupling.id
    client._manager.activate_coupling.assert_awaited_once()


def test_delete_coupling_deactivates_if_active(client: TestClient):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    resp = client.delete(f"/api/couplings/{coupling.id}")
    assert resp.status_code == 204
    client._manager.deactivate.assert_awaited_once()
    assert client._storage.get_coupling(coupling.id) is None


def test_deactivate_coupling_endpoint(client: TestClient):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    resp = client.post("/api/couplings/deactivate")
    assert resp.status_code == 200
    assert resp.json() == {"active_id": None}
    client._manager.deactivate.assert_awaited_once()
    assert client._storage.get_active_coupling_id() is None


def test_patch_coupling_render_field_triggers_update_render(client: TestClient):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    resp = client.patch(f"/api/couplings/{coupling.id}", json={"sensitivity": 1.8})
    assert resp.status_code == 200
    client._manager.update_render.assert_called_once()
    client._manager.deactivate.assert_not_awaited()
    client._manager.restart_cava.assert_not_awaited()


def test_patch_coupling_cava_field_triggers_restart_cava(client: TestClient):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    resp = client.patch(f"/api/couplings/{coupling.id}", json={"bars": 40})
    assert resp.status_code == 200
    client._manager.restart_cava.assert_awaited_once()
    client._manager.deactivate.assert_not_awaited()


def test_patch_coupling_deactivate_field_deactivates(client: TestClient):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    resp = client.patch(f"/api/couplings/{coupling.id}", json={"lms_host": "10.0.0.99"})
    assert resp.status_code == 200
    client._manager.deactivate.assert_awaited_once()


def test_patch_coupling_analysis_config_id_restarts_cava_not_deactivate(client: TestClient):
    """Swapping analysis_config_id on an active coupling must restart cava and
    rebuild the onset pipeline, but must NOT call deactivate()."""
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    new_ac = AnalysisConfig(name="AC2")
    client._storage.save_analysis_config(new_ac)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"analysis_config_id": new_ac.id},
    )
    assert resp.status_code == 200
    client._manager.deactivate.assert_not_awaited()
    client._manager.restart_cava.assert_awaited_once()
    client._manager.update_onset_pipeline.assert_called_once()
    client._manager.update_render.assert_called_once()

    saved = client._storage.get_coupling(coupling.id)
    assert saved is not None and saved.analysis_config_id == new_ac.id


def test_patch_coupling_render_config_id_update_render_only(client: TestClient):
    """Swapping render_config_id must call update_render only — no cava restart,
    no deactivate."""
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    new_rc = RenderConfig(name="RC2")
    client._storage.save_render_config(new_rc)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"render_config_id": new_rc.id},
    )
    assert resp.status_code == 200
    client._manager.deactivate.assert_not_awaited()
    client._manager.restart_cava.assert_not_awaited()
    client._manager.update_render.assert_called_once()

    saved = client._storage.get_coupling(coupling.id)
    assert saved is not None and saved.render_config_id == new_rc.id


def test_ac_swap_session_remains_active(client: TestClient):
    """After swapping analysis_config_id on an active coupling the session is
    not deactivated, restart_cava + update_onset_pipeline are called once
    (the live-update path), and the profile saved to storage is rebuilt from
    the NEW AC's settings.

    This is the canonical regression guard for the 'frozen lights' bug fixed
    in the 2026-09-06 routing refactor: analysis_config_id was in
    _C_DEACTIVATE_FIELDS (wrong) instead of _C_LIVE_FK_FIELDS, and
    restart_cava() used stale session.coupling (wrong).
    """
    # AC1: default bars=30, onset_delta=0.1
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    # AC2: deliberately different settings so we can distinguish old from new
    # in the saved profile.
    ac2 = AnalysisConfig(name="AC2", bars=50, onset_delta=0.5)
    client._storage.save_analysis_config(ac2)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"analysis_config_id": ac2.id},
    )
    assert resp.status_code == 200

    # Session must still be active — deactivate() is the wrong path.
    assert client._storage.get_active_coupling_id() == coupling.id
    client._manager.deactivate.assert_not_awaited()

    # Live-update path: cava restarted (picks up new bars) and onset pipeline
    # rebuilt (picks up new onset_delta).
    client._manager.restart_cava.assert_awaited_once()
    client._manager.update_onset_pipeline.assert_called_once()

    # Coupling in storage now points to AC2.
    saved_coupling = client._storage.get_coupling(coupling.id)
    assert saved_coupling is not None
    assert saved_coupling.analysis_config_id == ac2.id

    # AC2's settings are now in storage — verifies _apply_coupling_action()
    # used the UPDATED coupling (new AC ID), not the stale in-memory one.
    saved_ac2 = client._storage.get_analysis_config(ac2.id)
    assert saved_ac2 is not None
    assert saved_ac2.bars == 50          # AC2, not AC1's default 30
    assert saved_ac2.onset_delta == 0.5  # AC2, not AC1's default 0.1


# ---------------------------------------------------------------------------
# Clone coupling
# ---------------------------------------------------------------------------


def test_clone_coupling_returns_new_id(client: TestClient):
    """The cloned Coupling must have a different ID than the original."""
    coupling = _make_full_coupling(client._storage)

    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != coupling.id
    assert body["name"] == f"{coupling.name} (copy)"


def test_clone_coupling_analysis_and_render_configs_are_independent(client: TestClient):
    """Cloned AnalysisConfig and RenderConfig have new IDs but identical field values."""
    coupling = _make_full_coupling(client._storage)
    orig_ac = client._storage.get_analysis_config(coupling.analysis_config_id)
    orig_rc = client._storage.get_render_config(coupling.render_config_id)

    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    body = resp.json()

    new_ac = client._storage.get_analysis_config(body["analysis_config_id"])
    new_rc = client._storage.get_render_config(body["render_config_id"])

    # New IDs — not the same sub-entity objects.
    assert new_ac is not None
    assert new_rc is not None
    assert new_ac.id != orig_ac.id
    assert new_rc.id != orig_rc.id

    # All field values identical to the originals.
    assert new_ac.onset_method == orig_ac.onset_method
    assert new_ac.onset_delta == orig_ac.onset_delta
    assert new_ac.onset_alpha == orig_ac.onset_alpha
    assert new_ac.bars == orig_ac.bars
    assert new_ac.lower_cutoff_freq == orig_ac.lower_cutoff_freq
    assert new_ac.higher_cutoff_freq == orig_ac.higher_cutoff_freq
    assert new_rc.effect == orig_rc.effect
    assert new_rc.sensitivity == orig_rc.sensitivity
    assert new_rc.brightness_floor == orig_rc.brightness_floor
    assert new_rc.bass_hz == orig_rc.bass_hz
    assert new_rc.mid_hz == orig_rc.mid_hz
    assert new_rc.exertion_clip == orig_rc.exertion_clip

    # Player and LightProvider are shared (same IDs).
    assert body["player_id"] == coupling.player_id
    assert body["light_provider_id"] == coupling.light_provider_id


def test_clone_coupling_modifying_clone_does_not_affect_original(client: TestClient):
    """Mutating the clone's AnalysisConfig must not change the original's."""
    coupling = _make_full_coupling(client._storage)

    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    clone_body = resp.json()

    # Patch the clone's onset_method via the coupling PATCH endpoint.
    patch_resp = client.patch(
        f"/api/couplings/{clone_body['id']}",
        json={"onset_method": "multiband"},
    )
    assert patch_resp.status_code == 200

    # Clone's AnalysisConfig now has multiband.
    clone_ac = client._storage.get_analysis_config(clone_body["analysis_config_id"])
    assert clone_ac.onset_method == "multiband"

    # Original's AnalysisConfig still has combined (unchanged).
    orig_ac = client._storage.get_analysis_config(coupling.analysis_config_id)
    assert orig_ac.onset_method == "combined"

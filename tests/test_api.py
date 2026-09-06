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
    BridgeConfig,
    Coupling,
    LightProvider,
    Player,
    Profile,
    RenderConfig,
)
from huesync.storage import Storage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "config.json")


def _make_mock_manager() -> MagicMock:
    manager = MagicMock()
    # Active profile
    manager.active_profile_id = None
    manager.detected_sync_master = None
    manager.latency_warning = None
    # New properties
    type(manager).applied_delay_ms = PropertyMock(return_value=0)
    type(manager).bridge_connected = PropertyMock(return_value=False)
    type(manager).process_status = PropertyMock(return_value={"squeezelite": False, "cava": False})
    type(manager).last_bars = PropertyMock(return_value=[])
    # Async methods
    manager.activate = AsyncMock()
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
# Bridges
# ---------------------------------------------------------------------------


def test_list_bridges_empty(client: TestClient):
    resp = client.get("/api/bridges")
    assert resp.status_code == 200
    assert resp.json() == []


def test_delete_bridge_returns_204(client: TestClient):
    # Store a bridge first so there is something to delete.
    bridge = BridgeConfig(name="Test", host="192.168.1.1")
    client._storage.save_bridge(bridge)

    resp = client.delete(f"/api/bridges/{bridge.id}")
    assert resp.status_code == 204

    assert client._storage.get_bridge(bridge.id) is None


def test_get_bridge_areas_404_for_unknown(client: TestClient):
    resp = client.get("/api/bridges/does-not-exist/areas")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def test_list_profiles_returns_list(client: TestClient):
    resp = client.get("/api/profiles")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_profile_returns_201(client: TestClient):
    payload = {
        "name": "Living room",
        "lms_host": "192.168.1.10",
        "bridge_id": "br-1",
        "entertainment_area_id": "ea-1",
    }
    resp = client.post("/api/profiles", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Living room"
    assert body["lms_host"] == "192.168.1.10"
    # player_mac should have been auto-generated
    assert body["player_mac"] != ""


def test_get_profile_404_for_unknown(client: TestClient):
    resp = client.get("/api/profiles/does-not-exist")
    assert resp.status_code == 404


def test_patch_profile_applies_partial_update(client: TestClient):
    # Create a profile to patch.
    profile = Profile(name="Original", lms_host="10.0.0.1")
    client._storage.save_profile(profile)

    resp = client.patch(f"/api/profiles/{profile.id}", json={"name": "Updated"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Updated"
    # lms_host must be unchanged.
    assert body["lms_host"] == "10.0.0.1"


def test_patch_profile_all_frontend_fields_accepted(client: TestClient):
    """PATCH /profiles/{id} must accept every field the frontend's handleSave() sends.

    ProfilePatchBody has extra="forbid", so any field the frontend sends that
    is not listed there produces a 422.  This test sends the full payload that
    ProfileEditor.handleSave() assembles — both the fields that existed before
    the multiband/superflux commits (alsa_device, light_count, enabled) and
    the new ones (onset_method, superflux_mu, superflux_lag) — and asserts
    200 OK.  Without this test the two groups of fields were independently
    missing from ProfilePatchBody and caused silent 422 errors in production
    while all other tests remained green.
    """
    profile = Profile(name="Before", lms_host="10.0.0.1")
    client._storage.save_profile(profile)

    full_payload = {
        # Pre-existing frontend fields that were never in ProfilePatchBody
        "alsa_device": "hw:CARD=Dummy,DEV=0",
        "light_count": 3,
        "enabled": True,
        # Fields that were in ProfilePatchBody all along
        "name": "After",
        "lms_host": "10.0.0.2",
        "lms_port": 3483,
        "player_name": "HueSync",
        "bridge_id": "br-1",
        "entertainment_area_id": "ea-1",
        "entertainment_area_name": "Living Room",
        "color_mode": "spectrum_rgb",
        "bars": 30,
        "sensitivity": 1.2,
        "brightness_floor": 0.1,
        "exertion_clip": 3.0,
        "onset_delta": 0.1,
        "onset_alpha": 0.9,
        "lower_cutoff_freq": 50,
        "higher_cutoff_freq": 12000,
        "bass_hz": 250,
        "mid_hz": 2000,
        # New fields added in the multiband/superflux commits
        "onset_method": "superflux",
        "superflux_mu": 4,
        "superflux_lag": 2,
    }

    resp = client.patch(f"/api/profiles/{profile.id}", json=full_payload)
    assert resp.status_code == 200, (
        f"PATCH with full frontend payload returned {resp.status_code}: {resp.text}\n"
        "Check that ProfilePatchBody lists every field the frontend sends."
    )
    body = resp.json()
    assert body["name"] == "After"
    assert body["onset_method"] == "superflux"
    assert body["superflux_mu"] == 4
    assert body["alsa_device"] == "hw:CARD=Dummy,DEV=0"
    assert body["light_count"] == 3
    assert body["enabled"] is True


def test_patch_profile_does_not_touch_other_fields(client: TestClient):
    profile = Profile(name="A", sensitivity=0.5, brightness_floor=0.3)
    client._storage.save_profile(profile)

    resp = client.patch(f"/api/profiles/{profile.id}", json={"sensitivity": 0.8})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sensitivity"] == 0.8
    assert body["brightness_floor"] == pytest.approx(0.3)


def test_delete_profile_returns_204(client: TestClient):
    profile = Profile(name="To delete")
    client._storage.save_profile(profile)

    resp = client.delete(f"/api/profiles/{profile.id}")
    assert resp.status_code == 204
    assert client._storage.get_profile(profile.id) is None


def test_deactivate_profile_returns_active_id_none(client: TestClient):
    resp = client.post("/api/profiles/deactivate")
    assert resp.status_code == 200
    assert resp.json() == {"active_id": None}


def test_deactivate_does_not_conflict_with_profile_id_route(client: TestClient):
    # "deactivate" must not be routed as a {profile_id} parameter.
    resp = client.post("/api/profiles/deactivate")
    assert resp.status_code == 200


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
    assert "active_profile_id" in body
    assert "sync_master" in body
    assert "applied_delay_ms" in body
    assert "latency_warning" in body
    assert "processes" in body
    assert "squeezelite" in body["processes"]
    assert "cava" in body["processes"]
    assert "bridge_connected" in body


# ---------------------------------------------------------------------------
# patch_profile routing — smart session update
# ---------------------------------------------------------------------------


def test_patch_player_field_deactivates_session(client: TestClient):
    """Changing a player-level field (lms_host) must trigger deactivate."""
    profile = Profile(name="R", lms_host="10.0.0.1")
    client._storage.save_profile(profile)
    client._manager.active_profile_id = profile.id

    resp = client.patch(f"/api/profiles/{profile.id}", json={"lms_host": "10.0.0.2"})
    assert resp.status_code == 200
    client._manager.deactivate.assert_awaited_once()
    client._manager.restart_cava.assert_not_awaited()
    client._manager.update_onset_pipeline.assert_not_called()


def test_patch_cava_field_restarts_cava_not_deactivate(client: TestClient):
    """Changing a cava-level field (lower_cutoff_freq) must restart cava, not deactivate."""
    profile = Profile(name="R", lower_cutoff_freq=50)
    client._storage.save_profile(profile)
    client._manager.active_profile_id = profile.id

    resp = client.patch(f"/api/profiles/{profile.id}", json={"lower_cutoff_freq": 80})
    assert resp.status_code == 200
    client._manager.restart_cava.assert_awaited_once()
    client._manager.deactivate.assert_not_awaited()
    client._manager.update_onset_pipeline.assert_not_called()


def test_patch_pcm_field_updates_onset_pipeline_not_cava_not_deactivate(client: TestClient):
    """Changing onset_method must call update_onset_pipeline, not deactivate or restart_cava."""
    profile = Profile(name="R", onset_method="combined")
    client._storage.save_profile(profile)
    client._manager.active_profile_id = profile.id

    resp = client.patch(f"/api/profiles/{profile.id}", json={"onset_method": "multiband"})
    assert resp.status_code == 200
    client._manager.update_onset_pipeline.assert_called_once()
    client._manager.deactivate.assert_not_awaited()
    client._manager.restart_cava.assert_not_awaited()


def test_patch_render_field_calls_update_render_only(client: TestClient):
    """Changing sensitivity (render-only) must call update_render and nothing else."""
    profile = Profile(name="R", sensitivity=1.0)
    client._storage.save_profile(profile)
    client._manager.active_profile_id = profile.id

    resp = client.patch(f"/api/profiles/{profile.id}", json={"sensitivity": 1.5})
    assert resp.status_code == 200
    client._manager.update_render.assert_called_once()
    client._manager.deactivate.assert_not_awaited()
    client._manager.restart_cava.assert_not_awaited()
    client._manager.update_onset_pipeline.assert_not_called()


def test_patch_player_field_wins_over_render(client: TestClient):
    """When player and render fields change together, deactivate wins (player > render)."""
    profile = Profile(name="R", lms_host="10.0.0.1", sensitivity=1.0)
    client._storage.save_profile(profile)
    client._manager.active_profile_id = profile.id

    resp = client.patch(
        f"/api/profiles/{profile.id}",
        json={"lms_host": "10.0.0.2", "sensitivity": 1.5},
    )
    assert resp.status_code == 200
    client._manager.deactivate.assert_awaited_once()
    client._manager.restart_cava.assert_not_awaited()
    client._manager.update_onset_pipeline.assert_not_called()


def test_patch_cava_wins_over_pcm(client: TestClient):
    """When cava and pcm fields change together, restart_cava is called (cava > pcm).

    update_onset_pipeline is ALSO called — cava restart handles the cava process
    but the PCM pipeline still needs rebuilding for onset_method changes.
    """
    profile = Profile(name="R", lower_cutoff_freq=50, onset_method="combined")
    client._storage.save_profile(profile)
    client._manager.active_profile_id = profile.id

    resp = client.patch(
        f"/api/profiles/{profile.id}",
        json={"lower_cutoff_freq": 80, "onset_method": "multiband"},
    )
    assert resp.status_code == 200
    client._manager.restart_cava.assert_awaited_once()
    client._manager.deactivate.assert_not_awaited()
    # PCM pipeline rebuild runs in addition to cava restart.
    client._manager.update_onset_pipeline.assert_called_once()


def test_patch_inactive_profile_no_session_action(client: TestClient):
    """Patching an inactive profile must not call deactivate or any live-update method."""
    profile = Profile(name="R", lms_host="10.0.0.1", onset_method="combined", sensitivity=1.0)
    client._storage.save_profile(profile)
    # manager.active_profile_id stays None — profile is not active.

    resp = client.patch(
        f"/api/profiles/{profile.id}",
        json={"lms_host": "10.0.0.2", "onset_method": "multiband", "sensitivity": 1.5},
    )
    assert resp.status_code == 200
    client._manager.deactivate.assert_not_awaited()
    client._manager.restart_cava.assert_not_awaited()
    client._manager.update_onset_pipeline.assert_not_called()
    client._manager.update_render.assert_not_called()


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
# Players
# ---------------------------------------------------------------------------


def test_create_and_list_players(client: TestClient):
    payload = {"name": "Main Player", "lms_host": "10.0.0.5"}
    resp = client.post("/api/players", json=payload)
    assert resp.status_code == 201

    resp2 = client.get("/api/players")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 1
    assert resp2.json()[0]["lms_host"] == "10.0.0.5"


def test_create_and_get_player(client: TestClient):
    payload = {"name": "Bedroom", "lms_host": "10.0.0.6", "lms_port": 9000}
    resp = client.post("/api/players", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    player_id = body["id"]
    # MAC should be auto-generated if not provided
    assert body["player_mac"] != ""

    resp2 = client.get(f"/api/players/{player_id}")
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
    payload = {"name": "Vivid", "color_mode": "spectrum_rgb", "sensitivity": 1.5}
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
    player = Player(name="P", lms_host="10.0.0.1", player_mac="aa:bb:cc:dd:ee:ff")
    lp = LightProvider(name="LP", controller_id="ctrl-1", entertainment_area_id="ea-1")
    ac = AnalysisConfig(name="AC")
    rc = RenderConfig(name="RC")
    storage.save_player(player)
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
    player = Player(name="P", lms_host="10.0.0.1")
    lp = LightProvider(name="LP", controller_id="c1", entertainment_area_id="ea-1")
    ac = AnalysisConfig(name="AC")
    rc = RenderConfig(name="RC")
    client._storage.save_player(player)
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

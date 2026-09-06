"""One-time migration from the flat Profile model to the five-entity model.

Called on every startup (idempotent): profiles/bridges already migrated are
detected by coupling.id == profile.id and skipped.  New profiles added via
the old API after the first migration run are picked up on the next restart.

Migration strategy (per the design doc):
  - One Profile  → Player + LightProvider + AnalysisConfig + RenderConfig +
                    Coupling  (1-to-1; sharing can be set up via UI later)
  - One Bridge   → Controller (type=hue); controller.id == bridge.id so
                    all FK references are preserved without a mapping table.
  - Coupling.id  == Profile.id so active_profile_id still resolves correctly.
"""

from __future__ import annotations

import logging

from .models import (
    AnalysisConfig,
    Controller,
    ControllerType,
    Coupling,
    LightProvider,
    Player,
    RenderConfig,
)
from .storage import Storage

log = logging.getLogger(__name__)


def migrate_profiles_to_entities(storage: Storage) -> None:
    """Migrate Profiles and Bridges to the five-entity model.

    Safe to call on every startup: already-migrated records are skipped.
    """
    _migrate_bridges_to_controllers(storage)
    _migrate_profiles_to_couplings(storage)


def _migrate_bridges_to_controllers(storage: Storage) -> None:
    existing_controller_ids = {c.id for c in storage.list_controllers()}
    bridges = storage.list_bridges()

    for bridge in bridges:
        if bridge.id in existing_controller_ids:
            continue
        controller = Controller(
            id=bridge.id,
            name=bridge.name,
            type=ControllerType.HUE,
            host=bridge.host,
            app_key=bridge.app_key,
            client_key=bridge.client_key,
        )
        storage.save_controller(controller)
        log.info("Migrated bridge %r → Controller %s", bridge.name, controller.id)


def _migrate_profiles_to_couplings(storage: Storage) -> None:
    existing_coupling_ids = {c.id for c in storage.list_couplings()}
    profiles = storage.list_profiles()

    for profile in profiles:
        if profile.id in existing_coupling_ids:
            continue

        player = Player(
            name=profile.name,
            lms_host=profile.lms_host,
            lms_port=profile.lms_port,
            player_name=profile.player_name,
            player_mac=profile.player_mac,
            alsa_device=profile.alsa_device,
        )
        storage.save_player(player)

        area_name = profile.entertainment_area_name or profile.name
        light_provider = LightProvider(
            name=area_name,
            controller_id=profile.bridge_id,
            entertainment_area_id=profile.entertainment_area_id,
            entertainment_area_name=profile.entertainment_area_name,
            light_count=profile.light_count,
        )
        storage.save_light_provider(light_provider)

        analysis_config = AnalysisConfig(
            name=f"{profile.name} analysis",
            onset_method=profile.onset_method,
            onset_delta=profile.onset_delta,
            onset_alpha=profile.onset_alpha,
            superflux_mu=profile.superflux_mu,
            superflux_lag=profile.superflux_lag,
            bars=profile.bars,
            lower_cutoff_freq=profile.lower_cutoff_freq,
            higher_cutoff_freq=profile.higher_cutoff_freq,
        )
        storage.save_analysis_config(analysis_config)

        render_config = RenderConfig(
            name=f"{profile.name} render",
            color_mode=profile.color_mode,
            sensitivity=profile.sensitivity,
            brightness_floor=profile.brightness_floor,
            bass_hz=profile.bass_hz,
            mid_hz=profile.mid_hz,
            exertion_clip=profile.exertion_clip,
        )
        storage.save_render_config(render_config)

        coupling = Coupling(
            id=profile.id,
            name=profile.name,
            player_id=player.id,
            analysis_config_id=analysis_config.id,
            light_provider_id=light_provider.id,
            render_config_id=render_config.id,
            enabled=profile.enabled,
        )
        storage.save_coupling(coupling)

        log.info("Migrated profile %r → Coupling %s", profile.name, coupling.id)

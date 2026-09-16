"""Explicit, one-time historical JSON migration. Stop HueSync before invoking.

Only this module knows historical LightProvider/AnalysisConfig/Crossfader names.
No runtime fallback is installed. Conflicting aliases or unknown data fail closed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import tempfile
import uuid
from pathlib import Path

from .schema import SCHEMA_VERSION, empty_config, validate_current


def rename(row: dict, old: str, new: str) -> None:
    if old in row:
        if new in row and row[new] != row[old]:
            raise ValueError(f"Conflicting historical/current fields: {old} and {new}")
        row[new] = row.pop(old)


def convert(original: dict) -> dict:
    if not isinstance(original, dict):
        raise ValueError("Configuration must be a JSON object")
    if "schema_version" in original and type(original["schema_version"]) is not int:
        raise ValueError("Schema version must be an integer")
    if original.get("schema_version") == SCHEMA_VERSION:
        validate_current(original, references=True)
        return copy.deepcopy(original)
    if original.get("schema_version", 0) != 0:
        raise ValueError("Unsupported schema version")
    data = copy.deepcopy(original)
    if "active_profile_id" in data:
        if data["active_profile_id"] is not None:
            raise ValueError("Pre-entity active Profile cannot be mapped without its entities")
        del data["active_profile_id"]
    for old, new in (
        ("players", "virtual_players"), ("light_providers", "zones"),
        ("analysis_configs", "analysers"), ("render_configs", "effects"),
        ("scenes", "effects"), ("crossfaders", "energy_profiles"),
    ):
        rename(data, old, new)
    for key, value in empty_config().items():
        data.setdefault(key, value)
    for collection in ("virtual_players", "effects", "energy_profiles", "couplings"):
        if not isinstance(data[collection], list) or any(
            not isinstance(row, dict) for row in data[collection]
        ):
            raise ValueError(f"Malformed {collection}")
    for player in data["virtual_players"]:
        # A retired display label is retained as the current advertised name.
        rename(player, "name", "display_name")
    effects = {row.get("id"): row for row in data["effects"]}
    consumed_blend_settings = set()
    for row in data["couplings"]:
        for old, new in (("light_provider_id", "zone_id"),
                         ("analysis_config_id", "analyser_id"),
                         ("crossfader_id", "energy_profile_id")):
            rename(row, old, new)
        if "render_config_id" in row:
            if row.get("energy_profile_id"):
                raise ValueError("Both render_config_id and energy_profile_id present")
            high = row.pop("render_config_id")
            effect = effects.get(high)
            if effect is None:
                raise ValueError(f"Missing historical Effect {high!r}")
            consumed_blend_settings.add(id(effect))
            low = row.pop("mellow_render_config_id", "")
            mellow = effect.get("mellow_colour_mode")
            if not low and mellow and mellow != effect.get("color_mode", effect.get("effect_type")):
                low = str(uuid.uuid5(uuid.NAMESPACE_URL, 'huesync:mellow:' + row['id']))
                clone = dict(effect, id=low, name=effect.get("name", "Effect") + " (Low)")
                clone.pop("effect_type", None)
                clone["color_mode"] = mellow
                data["effects"].append(clone)
                consumed_blend_settings.add(id(clone))
            ep = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, 'huesync:energy:' + row['id'])),
                  "name": row.get("name", "Coupling") + " EnergyProfile",
                  "high_energy_effect_id": high, "low_energy_effect_id": low}
            for old, new, default in (("mix_low_threshold", "blend_start", .3),
                                      ("mix_high_threshold", "blend_end", .7),
                                      ("mix_ema_alpha", "blend_response", .1)):
                if old in row and old in effect and row[old] != effect[old]:
                    raise ValueError(f"Conflicting blend setting {old}")
                ep[new] = row.pop(old, effect.get(old, default))
            data["energy_profiles"].append(ep)
            row["energy_profile_id"] = ep["id"]
    for ep in data["energy_profiles"]:
        for old, new in (("active_scene_id", "high_energy_effect_id"),
                         ("mellow_scene_id", "low_energy_effect_id"),
                         ("low_threshold", "blend_start"), ("high_threshold", "blend_end"),
                         ("fade_speed", "blend_response")):
            rename(ep, old, new)
    for effect in data["effects"]:
        rename(effect, "color_mode", "effect_type")
        rename(effect, "effect", "effect_type")
        # Blend fields were moved above; retain the original bytes in the backup.
        # Unreferenced obsolete blend/mellow settings cannot be converted safely.
        historical = {k for k in effect if k in (
            "mellow_colour_mode", "mix_low_threshold", "mix_high_threshold", "mix_ema_alpha")}
        if historical and id(effect) not in consumed_blend_settings:
            raise ValueError(
                "Historical Effect blend settings were not migrated; refusing data loss")
        for key in historical:
            effect.pop(key)
    data["schema_version"] = SCHEMA_VERSION
    validate_current(data, references=True)
    return data


def migrate_file(path: Path, *, check: bool = False) -> bool:
    """Validate first, backup exact bytes, atomically replace. Return migration-needed.

    Caller must stop the runtime. The installer lock serializes deployments;
    the source is rechecked before replace to detect concurrent external edits.
    """
    if not path.exists():
        if check:
            raise ValueError(f"Missing configuration: {path}")
        data = empty_config()
        raw = None
    else:
        raw = path.read_bytes()
        original = json.loads(raw)
        data = convert(original)
        if data == original:
            return False
    if check:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = path.stat() if raw is not None else None
    if raw is not None:
        digest = hashlib.sha256(raw).hexdigest()
        backup = path.with_name(f"{path.name}.pre-v{SCHEMA_VERSION}.{digest}.bak")
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if backup.read_bytes() != raw:
                raise ValueError("Existing backup differs; refusing migration") from None
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        print(f"Backup: {backup}")
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if metadata:
            os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        if raw is not None and path.read_bytes() != raw:
            raise ValueError("Configuration changed during migration")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        needed = migrate_file(args.path, check=args.check)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(1, f'Migration failed safely: {exc}\n')
    status = 'migration required' if args.check and needed else 'valid'
    print(f'Schema {SCHEMA_VERSION}: {status}')
    if args.check and needed:
        parser.exit(1)


if __name__ == '__main__':
    main()

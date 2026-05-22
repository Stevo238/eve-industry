"""
EVE SDE (Static Data Export) importer.

Downloads the official CCP SDE ZIP from:
  https://eve-static-data-export.s3-eu-west-1.amazonaws.com/tranquility/sde.zip

Parses and imports:
  - typeIDs.yaml     → sde_types
  - groupIDs.yaml    → sde_groups
  - categoryIDs.yaml → sde_categories
  - marketGroups.yaml→ sde_market_groups
  - blueprints.yaml  → sde_blueprint_activities, sde_blueprint_materials,
                       sde_blueprint_products, sde_blueprint_skills

The SDE ZIP is ~100MB compressed / ~400MB uncompressed.
Import may take 5-10 minutes depending on hardware.
"""

import asyncio
import io
import zipfile

import httpx
import yaml

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.sde import (
    SdeBlueprintActivity,
    SdeBlueprintMaterial,
    SdeBlueprintProduct,
    SdeBlueprintSkill,
    SdeCategory,
    SdeGroup,
    SdeMarketGroup,
    SdeType,
)

CHUNK_SIZE = 500  # rows per DB commit batch

# Each entry is a list of candidate filenames (CCP has renamed files across SDE versions)
SDE_FILE_CANDIDATES = {
    "typeIDs":     ["types.yaml",      "typeIDs.yaml"],
    "groupIDs":    ["groups.yaml",     "groupIDs.yaml"],
    "categoryIDs": ["categories.yaml", "categoryIDs.yaml"],
    "marketGroups":["marketGroups.yaml"],
    "blueprints":  ["blueprints.yaml"],
}


def _build_file_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """Find each required file anywhere in the ZIP, trying candidate names in order."""
    all_yaml = [n for n in zf.namelist() if n.endswith(".yaml")]
    result = {}
    for key, candidates in SDE_FILE_CANDIDATES.items():
        found = None
        for filename in candidates:
            matches = [n for n in all_yaml if n.endswith("/" + filename) or n == filename]
            if matches:
                found = matches[0]
                break
        if found is None:
            raise FileNotFoundError(
                f"Could not find {candidates} in SDE ZIP.\n"
                f"ZIP contains: {all_yaml[:30]}"
            )
        result[key] = found
    return result


async def run_import(status: dict) -> None:
    """Download and import the full SDE. Updates status dict in-place."""

    status["progress"] = "Downloading SDE from CCP servers (~100MB)..."

    # Stream download into memory
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        response = await client.get(settings.eve_sde_url)
        response.raise_for_status()
        raw_zip = response.content

    status["progress"] = f"Download complete ({len(raw_zip) // 1_048_576}MB). Extracting..."

    zf = zipfile.ZipFile(io.BytesIO(raw_zip))
    files = _build_file_map(zf)

    async with AsyncSessionLocal() as db:
        # ----------------------------------------------------------------
        # Categories
        # ----------------------------------------------------------------
        status["progress"] = "Importing categories..."
        data = await asyncio.to_thread(yaml.safe_load, zf.read(files["categoryIDs"]))
        await _clear_table(db, SdeCategory)
        batch = []
        for cat_id, info in data.items():
            batch.append(
                SdeCategory(
                    category_id=cat_id,
                    name=_en(info.get("name")) or f"Category {cat_id}",
                    published=int(info.get("published", True)),
                )
            )
            if len(batch) >= CHUNK_SIZE:
                db.add_all(batch)
                await db.commit()
                batch.clear()
        if batch:
            db.add_all(batch)
            await db.commit()

        # ----------------------------------------------------------------
        # Groups
        # ----------------------------------------------------------------
        status["progress"] = "Importing groups..."
        data = await asyncio.to_thread(yaml.safe_load, zf.read(files["groupIDs"]))
        await _clear_table(db, SdeGroup)
        batch = []
        for grp_id, info in data.items():
            batch.append(
                SdeGroup(
                    group_id=grp_id,
                    name=_en(info.get("name")) or f"Group {grp_id}",
                    category_id=info.get("categoryID", 0),
                    published=int(info.get("published", True)),
                )
            )
            if len(batch) >= CHUNK_SIZE:
                db.add_all(batch)
                await db.commit()
                batch.clear()
        if batch:
            db.add_all(batch)
            await db.commit()

        # ----------------------------------------------------------------
        # Market groups
        # ----------------------------------------------------------------
        status["progress"] = "Importing market groups..."
        data = await asyncio.to_thread(yaml.safe_load, zf.read(files["marketGroups"]))
        await _clear_table(db, SdeMarketGroup)
        batch = []
        for mg_id, info in data.items():
            batch.append(
                SdeMarketGroup(
                    market_group_id=mg_id,
                    name=_en(info.get("name")) or f"Group {mg_id}",
                    parent_group_id=info.get("parentGroupID"),
                    description=_en(info.get("description")),
                )
            )
            if len(batch) >= CHUNK_SIZE:
                db.add_all(batch)
                await db.commit()
                batch.clear()
        if batch:
            db.add_all(batch)
            await db.commit()

        # ----------------------------------------------------------------
        # Types  (largest file — ~50k types)
        # ----------------------------------------------------------------
        status["progress"] = "Importing types (this may take a few minutes)..."
        data = await asyncio.to_thread(yaml.safe_load, zf.read(files["typeIDs"]))
        await _clear_table(db, SdeType)
        batch = []
        for type_id, info in data.items():
            batch.append(
                SdeType(
                    type_id=type_id,
                    name=_en(info.get("name")) or str(type_id),
                    description=_en(info.get("description")),
                    group_id=info.get("groupID", 0),
                    market_group_id=info.get("marketGroupID"),
                    mass=info.get("mass"),
                    volume=info.get("volume"),
                    packaged_volume=info.get("packagedVolume"),
                    portion_size=info.get("portionSize", 1),
                    published=int(info.get("published", True)),
                )
            )
            if len(batch) >= CHUNK_SIZE:
                db.add_all(batch)
                await db.commit()
                batch.clear()
        if batch:
            db.add_all(batch)
            await db.commit()

        # Set category_id on types via group lookup (join query approach)
        status["progress"] = "Linking type categories..."
        from sqlalchemy import text
        await db.execute(
            text(
                "UPDATE sde_types SET category_id = ("
                "  SELECT category_id FROM sde_groups WHERE sde_groups.group_id = sde_types.group_id"
                ")"
            )
        )
        await db.commit()

        # ----------------------------------------------------------------
        # Blueprints
        # ----------------------------------------------------------------
        status["progress"] = "Importing blueprints..."
        bp_data = await asyncio.to_thread(yaml.safe_load, zf.read(files["blueprints"]))

        await _clear_table(db, SdeBlueprintActivity)
        await _clear_table(db, SdeBlueprintMaterial)
        await _clear_table(db, SdeBlueprintProduct)
        await _clear_table(db, SdeBlueprintSkill)

        act_batch, mat_batch, prod_batch, skill_batch = [], [], [], []

        # Dedup sets — CCP's SDE sometimes contains duplicate rows
        seen_acts   = set()
        seen_mats   = set()
        seen_prods  = set()
        seen_skills = set()

        ACTIVITY_IDS = {
            "manufacturing": 1,
            "researching_time_efficiency": 3,
            "researching_material_efficiency": 4,
            "copying": 5,
            "invention": 8,
            "reaction": 11,
        }

        for bp_type_id, bp_info in bp_data.items():
            max_limit = bp_info.get("maxProductionLimit")
            activities = bp_info.get("activities", {})

            for act_name, act_data in activities.items():
                act_id = ACTIVITY_IDS.get(act_name)
                if act_id is None:
                    continue

                act_key = (bp_type_id, act_id)
                if act_key not in seen_acts:
                    seen_acts.add(act_key)
                    act_batch.append(
                        SdeBlueprintActivity(
                            blueprint_type_id=bp_type_id,
                            activity_id=act_id,
                            time=act_data.get("time", 0),
                            max_production_limit=max_limit if act_id == 1 else None,
                        )
                    )

                for mat in act_data.get("materials", []):
                    mat_key = (bp_type_id, act_id, mat["typeID"])
                    if mat_key not in seen_mats:
                        seen_mats.add(mat_key)
                        mat_batch.append(
                            SdeBlueprintMaterial(
                                blueprint_type_id=bp_type_id,
                                activity_id=act_id,
                                material_type_id=mat["typeID"],
                                quantity=mat["quantity"],
                            )
                        )

                for prod in act_data.get("products", []):
                    prod_key = (bp_type_id, act_id, prod["typeID"])
                    if prod_key not in seen_prods:
                        seen_prods.add(prod_key)
                        prod_batch.append(
                            SdeBlueprintProduct(
                                blueprint_type_id=bp_type_id,
                                activity_id=act_id,
                                product_type_id=prod["typeID"],
                                quantity=prod["quantity"],
                                probability=prod.get("probability"),
                            )
                        )

                for skill in act_data.get("skills", []):
                    skill_key = (bp_type_id, act_id, skill["typeID"])
                    if skill_key not in seen_skills:
                        seen_skills.add(skill_key)
                        skill_batch.append(
                            SdeBlueprintSkill(
                                blueprint_type_id=bp_type_id,
                                activity_id=act_id,
                                skill_type_id=skill["typeID"],
                                level=skill["level"],
                            )
                        )

            # Flush batches periodically
            if len(act_batch) >= CHUNK_SIZE:
                db.add_all(act_batch)
                db.add_all(mat_batch)
                db.add_all(prod_batch)
                db.add_all(skill_batch)
                await db.commit()
                act_batch.clear()
                mat_batch.clear()
                prod_batch.clear()
                skill_batch.clear()

        # Final flush
        if act_batch:
            db.add_all(act_batch)
        if mat_batch:
            db.add_all(mat_batch)
        if prod_batch:
            db.add_all(prod_batch)
        if skill_batch:
            db.add_all(skill_batch)
        await db.commit()

    type_total = len(data)
    bp_total = len(bp_data)
    status["progress"] = (
        f"Done! Imported {type_total:,} types, {bp_total:,} blueprints."
    )


def _en(field) -> str | None:
    """Extract English string from SDE localised string field."""
    if field is None:
        return None
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        return field.get("en") or next(iter(field.values()), None)
    return str(field)


async def _clear_table(db, model) -> None:
    from sqlalchemy import delete
    await db.execute(delete(model))
    await db.commit()

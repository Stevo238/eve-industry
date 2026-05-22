"""
ESI data synchronisation functions.
Each function fetches fresh data from ESI and upserts it into the local DB.
"""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.esi.client import ESIClient
from app.models.assets import Asset
from app.models.blueprints import Blueprint
from app.models.character import Character
from app.models.industry import CharacterSkill, IndustryCostIndex, IndustryJob
from app.models.market import CharacterOrder, MarketPrice


# ---------------------------------------------------------------------------
# Character public info
# ---------------------------------------------------------------------------


async def sync_character_info(db: AsyncSession, character_id: int) -> None:
    esi = ESIClient(db)
    try:
        data = await esi.get(f"/characters/{character_id}/")
    finally:
        await esi.close()

    result = await db.execute(select(Character).where(Character.character_id == character_id))
    char = result.scalar_one_or_none()
    if char is None:
        return

    char.corporation_id = data.get("corporation_id")
    char.alliance_id = data.get("alliance_id")
    await db.commit()


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------


async def sync_wallet(db: AsyncSession, character_id: int) -> None:
    esi = ESIClient(db)
    try:
        balance = await esi.get(f"/characters/{character_id}/wallet/", character_id=character_id)
    finally:
        await esi.close()

    result = await db.execute(select(Character).where(Character.character_id == character_id))
    char = result.scalar_one_or_none()
    if char:
        char.wallet_balance = float(balance)
        await db.commit()


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


async def sync_assets(db: AsyncSession, character_id: int) -> int:
    """Returns count of assets synced."""
    esi = ESIClient(db)
    try:
        items = await esi.get_paginated(
            f"/characters/{character_id}/assets/", character_id=character_id
        )
    finally:
        await esi.close()

    await db.execute(delete(Asset).where(Asset.character_id == character_id))

    for item in items:
        db.add(
            Asset(
                item_id=item["item_id"],
                character_id=character_id,
                type_id=item["type_id"],
                location_id=item["location_id"],
                location_type=item["location_type"],
                location_flag=item["location_flag"],
                quantity=item.get("quantity", 1),
                is_singleton=item.get("is_singleton", False),
                is_blueprint_copy=item.get("is_blueprint_copy"),
                last_updated=datetime.utcnow(),
            )
        )

    await db.commit()
    return len(items)


# ---------------------------------------------------------------------------
# Blueprints
# ---------------------------------------------------------------------------


async def sync_blueprints(db: AsyncSession, character_id: int) -> int:
    esi = ESIClient(db)
    try:
        items = await esi.get_paginated(
            f"/characters/{character_id}/blueprints/", character_id=character_id
        )
    finally:
        await esi.close()

    await db.execute(delete(Blueprint).where(Blueprint.character_id == character_id))

    for item in items:
        db.add(
            Blueprint(
                item_id=item["item_id"],
                character_id=character_id,
                type_id=item["type_id"],
                location_id=item["location_id"],
                location_flag=item["location_flag"],
                material_efficiency=item.get("material_efficiency", 0),
                time_efficiency=item.get("time_efficiency", 0),
                runs=item.get("runs", -1),
                last_updated=datetime.utcnow(),
            )
        )

    await db.commit()
    return len(items)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def sync_skills(db: AsyncSession, character_id: int) -> int:
    esi = ESIClient(db)
    try:
        data = await esi.get(
            f"/characters/{character_id}/skills/", character_id=character_id
        )
    finally:
        await esi.close()

    await db.execute(
        delete(CharacterSkill).where(CharacterSkill.character_id == character_id)
    )

    skills = data.get("skills", [])
    for skill in skills:
        db.add(
            CharacterSkill(
                character_id=character_id,
                skill_id=skill["skill_id"],
                trained_level=skill["trained_skill_level"],
                active_level=skill["active_skill_level"],
                skillpoints_in_skill=skill["skillpoints_in_skill"],
                last_updated=datetime.utcnow(),
            )
        )

    await db.commit()
    return len(skills)


# ---------------------------------------------------------------------------
# Industry jobs
# ---------------------------------------------------------------------------


async def sync_industry_jobs(db: AsyncSession, character_id: int) -> int:
    esi = ESIClient(db)
    try:
        items = await esi.get(
            f"/characters/{character_id}/industry/jobs/",
            character_id=character_id,
            params={"include_completed": True},
        )
    finally:
        await esi.close()

    await db.execute(
        delete(IndustryJob).where(IndustryJob.character_id == character_id)
    )

    def _dt(s: str | None) -> datetime | None:
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)

    for job in items:
        db.add(
            IndustryJob(
                job_id=job["job_id"],
                character_id=character_id,
                installer_id=job["installer_id"],
                facility_id=job["facility_id"],
                station_id=job["station_id"],
                activity_id=job["activity_id"],
                blueprint_id=job["blueprint_id"],
                blueprint_type_id=job["blueprint_type_id"],
                blueprint_location_id=job["blueprint_location_id"],
                output_location_id=job["output_location_id"],
                runs=job["runs"],
                cost=job.get("cost"),
                licensed_runs=job.get("licensed_runs"),
                probability=job.get("probability"),
                product_type_id=job.get("product_type_id"),
                status=job["status"],
                duration=job["duration"],
                start_date=_dt(job["start_date"]),
                end_date=_dt(job["end_date"]),
                pause_date=_dt(job.get("pause_date")),
                completed_date=_dt(job.get("completed_date")),
                completed_character_id=job.get("completed_character_id"),
                successful_runs=job.get("successful_runs"),
                last_updated=datetime.utcnow(),
            )
        )

    await db.commit()
    return len(items)


# ---------------------------------------------------------------------------
# Character market orders
# ---------------------------------------------------------------------------


async def sync_character_orders(db: AsyncSession, character_id: int) -> int:
    esi = ESIClient(db)
    try:
        items = await esi.get(
            f"/characters/{character_id}/orders/", character_id=character_id
        )
    finally:
        await esi.close()

    await db.execute(
        delete(CharacterOrder).where(CharacterOrder.character_id == character_id)
    )

    def _dt(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)

    for order in items:
        db.add(
            CharacterOrder(
                order_id=order["order_id"],
                character_id=character_id,
                type_id=order["type_id"],
                location_id=order["location_id"],
                region_id=order["region_id"],
                price=order["price"],
                volume_remain=order["volume_remain"],
                volume_total=order["volume_total"],
                is_buy_order=order.get("is_buy_order", False),
                duration=order["duration"],
                issued=_dt(order["issued"]),
                escrow=order.get("escrow"),
                is_corporation=order.get("is_corporation", False),
                state=order.get("state", "active"),
                last_updated=datetime.utcnow(),
            )
        )

    await db.commit()
    return len(items)


# ---------------------------------------------------------------------------
# Market prices (global, no auth required)
# ---------------------------------------------------------------------------


async def sync_market_prices(db: AsyncSession) -> int:
    esi = ESIClient(db)
    try:
        items = await esi.get("/markets/prices/")
    finally:
        await esi.close()

    await db.execute(delete(MarketPrice))

    for item in items:
        db.add(
            MarketPrice(
                type_id=item["type_id"],
                adjusted_price=item.get("adjusted_price"),
                average_price=item.get("average_price"),
                last_updated=datetime.utcnow(),
            )
        )

    await db.commit()
    return len(items)


# ---------------------------------------------------------------------------
# Industry cost indexes (global, no auth required)
# ---------------------------------------------------------------------------


async def sync_industry_cost_indexes(db: AsyncSession) -> int:
    esi = ESIClient(db)
    try:
        systems = await esi.get("/industry/systems/")
    finally:
        await esi.close()

    await db.execute(delete(IndustryCostIndex))

    count = 0
    for system in systems:
        for index in system.get("cost_indices", []):
            db.add(
                IndustryCostIndex(
                    solar_system_id=system["solar_system_id"],
                    activity=index["activity"],
                    cost_index=index["cost_index"],
                    last_updated=datetime.utcnow(),
                )
            )
            count += 1

    await db.commit()
    return count


# ---------------------------------------------------------------------------
# Resolve type names from ESI for types missing from the SDE
# ---------------------------------------------------------------------------


async def resolve_missing_type_names(db: AsyncSession) -> int:
    """
    Find every type_id referenced in assets/blueprints that has no SDE entry,
    then bulk-resolve names via POST /universe/names/ and insert stub rows.
    Returns the number of new types added.
    """
    from app.models.assets import Asset
    from app.models.blueprints import Blueprint
    from app.models.sde import SdeType
    from sqlalchemy import text

    # Collect all referenced type_ids
    asset_ids = {row[0] for row in (await db.execute(select(Asset.type_id).distinct())).fetchall()}
    bp_ids    = {row[0] for row in (await db.execute(select(Blueprint.type_id).distinct())).fetchall()}
    all_ids   = asset_ids | bp_ids

    if not all_ids:
        return 0

    # Find which ones are already in the SDE
    existing = {
        row[0]
        for row in (
            await db.execute(
                select(SdeType.type_id).where(SdeType.type_id.in_(list(all_ids)))
            )
        ).fetchall()
    }

    missing = list(all_ids - existing)
    if not missing:
        return 0

    # Bulk-resolve via ESI /universe/names/ (max 1000 per request)
    import httpx
    resolved = []
    for i in range(0, len(missing), 1000):
        batch = missing[i : i + 1000]
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://esi.evetech.net/latest/universe/names/",
                json=batch,
                headers={"Accept": "application/json"},
            )
        if resp.status_code == 200:
            resolved.extend(resp.json())

    # Insert stub SdeType rows for each resolved name
    count = 0
    for item in resolved:
        if item.get("category") == "inventory_type":
            db.add(
                SdeType(
                    type_id=item["id"],
                    name=item["name"],
                    group_id=0,
                    portion_size=1,
                    published=0,
                )
            )
            count += 1

    if count:
        await db.commit()
    return count


# ---------------------------------------------------------------------------
# Resolve location IDs → human-readable names
# ---------------------------------------------------------------------------


async def resolve_locations(db: AsyncSession, character_id: int | None = None) -> tuple[int, dict]:
    """
    Resolve all location_ids referenced in assets into the locations cache.

    Phases:
      1. NPC stations / solar systems  → POST /universe/names/ (no auth)
      2. Player structures (id ≥ 1e12) → GET /universe/structures/{id}/ trying ALL characters
      3. Item-chain locations           → walk item_map to nearest station/structure
    """
    import httpx

    from app.models.assets import Asset
    from app.models.location import Location
    from app.models.sde import SdeType

    # ── Build asset index ────────────────────────────────────────────────
    rows = (
        await db.execute(
            select(Asset.item_id, Asset.location_id, Asset.location_type, Asset.type_id)
        )
    ).fetchall()

    # item_id → (location_id, location_type, type_id)
    item_map: dict[int, tuple[int, str, int]] = {r[0]: (r[1], r[2], r[3]) for r in rows}
    all_loc_ids = list({r[1] for r in rows})

    if not all_loc_ids:
        return 0, {}  # type: ignore[return-value]

    # Already cached IDs — but exclude "Unknown Structure" entries so they are
    # always retried (a previous sync may have stored them when the token lacked
    # the scope; now we try again with potentially fresher/re-authed tokens).
    cached_rows = (
        await db.execute(
            select(Location.location_id, Location.name).where(
                Location.location_id.in_(all_loc_ids)
            )
        )
    ).fetchall()

    # Stale = cached but placeholder; delete them so they get re-resolved.
    # Catches all bad-name formats:
    #   "Unknown Structure (1047213331825)"
    #   "Porpoise @ 1047213331825"
    #   "Location 1047213331825"
    # Any name containing a 12+ digit raw ID is treated as unresolved.
    import re as _re
    _raw_id_pat = _re.compile(r'\d{12,}')
    stale_ids = {
        r[0]
        for r in cached_rows
        if "Unknown Structure" in r[1] or _raw_id_pat.search(r[1])
    }
    if stale_ids:
        from sqlalchemy import delete as sql_delete
        await db.execute(
            sql_delete(Location).where(Location.location_id.in_(stale_ids))
        )
        await db.commit()

    cached = {r[0] for r in cached_rows if r[0] not in stale_ids}
    missing_set = {i for i in all_loc_ids if i not in cached}
    print(f"[LOC] stale_deleted={len(stale_ids)} cached={len(cached)} missing={len(missing_set)}")
    if not missing_set:
        print("[LOC] nothing to resolve, all cached")
        return 0, {}

    # KEY FIX: only treat a location as "item" if the ID actually exists as an
    # asset item_id in item_map. Structure IDs are never in item_map, so they
    # always fall through to the structure resolver even if location_type == "item".
    item_type_loc_ids = {
        r[1]
        for r in rows
        if r[2] == "item" and r[1] in missing_set and r[1] in item_map
    }
    real_loc_ids   = [i for i in missing_set if i not in item_type_loc_ids]
    structure_ids  = [i for i in real_loc_ids if i >= 1_000_000_000_000]
    public_ids     = [i for i in real_loc_ids if i <  1_000_000_000_000]
    print(f"[LOC] structures={len(structure_ids)} public={len(public_ids)} item_chain={len(item_type_loc_ids)}")
    if structure_ids:
        print(f"[LOC] structure IDs to resolve: {structure_ids[:10]}")

    count = 0

    # ── Phase 1: NPC stations + solar systems ────────────────────────────
    resolved_public: set[int] = set()
    for i in range(0, len(public_ids), 1000):
        batch = public_ids[i : i + 1000]
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://esi.evetech.net/latest/universe/names/",
                json=batch,
                headers={"Accept": "application/json"},
            )
        if resp.status_code == 200:
            for item in resp.json():
                loc_type = {
                    "station": "station",
                    "solar_system": "solar_system",
                    "constellation": "constellation",
                    "region": "region",
                }.get(item.get("category", ""), "unknown")
                db.add(Location(
                    location_id=item["id"],
                    name=item["name"],
                    location_type=loc_type,
                    last_updated=datetime.utcnow(),
                ))
                resolved_public.add(item["id"])
                count += 1

    # Fallback for any public IDs ESI didn't return
    for loc_id in public_ids:
        if loc_id not in resolved_public:
            db.add(Location(
                location_id=loc_id,
                name=f"Location {loc_id}",
                location_type="unknown",
                last_updated=datetime.utcnow(),
            ))
            count += 1

    await db.commit()  # commit phase 1 before structure auth calls

    # ── Phase 2: Player structures — try every character token ───────────
    struct_errors: dict[int, list[str]] = {}
    if structure_ids:
        # Load all available character tokens
        all_char_ids = [
            r[0]
            for r in (await db.execute(
                select(Character.character_id)
            )).fetchall()
        ]

        for struct_id in structure_ids:
            name = None
            errs: list[str] = []
            for char_id in all_char_ids:
                esi = ESIClient(db)
                try:
                    data = await esi.get(
                        f"/universe/structures/{struct_id}/",
                        character_id=char_id,
                    )
                    name = data.get("name")
                    print(f"[LOC] structure {struct_id} via char {char_id} → {name!r}")
                    if name:
                        break
                except Exception as exc:
                    errs.append(f"char {char_id}: {exc}")
                    print(f"[LOC] structure {struct_id} via char {char_id} → ERROR: {exc}")
                finally:
                    await esi.close()

            if not name:
                struct_errors[struct_id] = errs
                print(f"[LOC] structure {struct_id} UNRESOLVED — storing placeholder")

            db.add(Location(
                location_id=struct_id,
                name=name or f"Unknown Structure ({struct_id})",
                location_type="structure",
                last_updated=datetime.utcnow(),
            ))
            count += 1

        await db.commit()  # commit phase 2 before building loc_cache

    # ── Phase 3: Item-chain locations (fitted/in cargo/containers) ───────
    if item_type_loc_ids:
        # Reload full location cache now that phases 1+2 are committed
        loc_cache: dict[int, str] = {
            r[0]: r[1]
            for r in (
                await db.execute(select(Location.location_id, Location.name))
            ).fetchall()
        }

        for parent_item_id in item_type_loc_ids:
            if parent_item_id in cached:
                continue

            visited: set[int] = set()
            cur_id = parent_item_id
            root_loc_name = ""
            parent_type_name = ""

            while cur_id in item_map:
                if cur_id in visited:
                    break
                visited.add(cur_id)
                cur_loc_id, cur_loc_type, cur_type_id = item_map[cur_id]

                if not parent_type_name:
                    t = await db.get(SdeType, cur_type_id)
                    parent_type_name = t.name if t else f"Item {cur_type_id}"

                if cur_loc_type == "item" and cur_loc_id in item_map:
                    cur_id = cur_loc_id          # keep walking up
                else:
                    # Reached a real location (station/structure/solar_system)
                    root_loc_name = loc_cache.get(cur_loc_id, "")
                    break
            else:
                root_loc_name = loc_cache.get(cur_id, "")

            display = parent_type_name or f"Item {parent_item_id}"
            if root_loc_name:
                display = f"{parent_type_name} @ {root_loc_name}"

            db.add(Location(
                location_id=parent_item_id,
                name=display,
                location_type="in_item",
                last_updated=datetime.utcnow(),
            ))
            count += 1

        await db.commit()

    return count, struct_errors


# ---------------------------------------------------------------------------
# Full sync for a single character
# ---------------------------------------------------------------------------


async def sync_character_all(db: AsyncSession, character_id: int) -> dict:
    results = {}
    errors = {}

    tasks = [
        ("assets", sync_assets),
        ("blueprints", sync_blueprints),
        ("skills", sync_skills),
        ("industry_jobs", sync_industry_jobs),
        ("market_orders", sync_character_orders),
        ("wallet", sync_wallet),
    ]

    for name, fn in tasks:
        try:
            result = await fn(db, character_id)
            results[name] = result if result is not None else True
        except Exception as exc:
            errors[name] = str(exc)

    # Resolve any type names missing from the SDE via ESI
    try:
        new_types = await resolve_missing_type_names(db)
        if new_types:
            results["new_type_names"] = new_types
    except Exception as exc:
        errors["type_names"] = str(exc)

    # Resolve location IDs to human-readable names
    try:
        new_locs, struct_errs = await resolve_locations(db, character_id)
        results["locations_resolved"] = new_locs
        if struct_errs:
            # Flatten for display: first error per structure
            errors["structure_resolution"] = "; ".join(
                f"{sid}: {errs[0]}" for sid, errs in struct_errs.items()
            )
    except Exception as exc:
        errors["locations"] = str(exc)

    # Update last_synced timestamp
    result = await db.execute(select(Character).where(Character.character_id == character_id))
    char = result.scalar_one_or_none()
    if char:
        char.last_synced = datetime.utcnow()
        await db.commit()

    return {"synced": results, "errors": errors}

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
# Market order prices — best Jita buy/sell per type
# ---------------------------------------------------------------------------


async def sync_blueprint_market_prices(db: AsyncSession, region_id: int = 10000002) -> int:
    """
    Fetch best Jita buy and sell prices for types used by the user's own
    blueprints (materials + products only — not the entire SDE).

    Uses The Forge region (10000002).  Concurrent batches of 20 per-type
    requests so the whole fetch completes in seconds, not minutes.
    """
    import asyncio
    import httpx

    from app.models.blueprints import Blueprint
    from app.models.market import MarketPrice
    from app.models.sde import SdeBlueprintMaterial, SdeBlueprintProduct
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    REGION_ID = region_id
    SUPPORTED_ACTIVITIES = [1, 11]  # manufacturing + reactions

    # ── Scope to blueprints the user actually owns ────────────────────────
    user_bp_type_ids = [
        r[0]
        for r in (await db.execute(select(Blueprint.type_id).distinct())).fetchall()
    ]
    if not user_bp_type_ids:
        print("[PRICE] No blueprints found in DB — nothing to fetch")
        return 0

    print(f"[PRICE] User owns {len(user_bp_type_ids)} blueprint types")

    mat_ids = {
        r[0]
        for r in (
            await db.execute(
                select(SdeBlueprintMaterial.material_type_id)
                .where(SdeBlueprintMaterial.blueprint_type_id.in_(user_bp_type_ids))
                .where(SdeBlueprintMaterial.activity_id.in_(SUPPORTED_ACTIVITIES))
                .distinct()
            )
        ).fetchall()
    }
    prod_ids = {
        r[0]
        for r in (
            await db.execute(
                select(SdeBlueprintProduct.product_type_id)
                .where(SdeBlueprintProduct.blueprint_type_id.in_(user_bp_type_ids))
                .where(SdeBlueprintProduct.activity_id.in_(SUPPORTED_ACTIVITIES))
                .distinct()
            )
        ).fetchall()
    }
    type_ids = list(mat_ids | prod_ids)
    print(f"[PRICE] Fetching prices for {len(type_ids)} types ({len(mat_ids)} mats, {len(prod_ids)} products)")

    if not type_ids:
        print("[PRICE] No material/product types found in SDE for user blueprints")
        return 0

    best_prices: dict[int, dict] = {}

    async def fetch_type(client: httpx.AsyncClient, type_id: int) -> None:
        for order_type, key in [("sell", "sell_price"), ("buy", "buy_price")]:
            try:
                resp = await client.get(
                    f"https://esi.evetech.net/latest/markets/{REGION_ID}/orders/",
                    params={"type_id": type_id, "order_type": order_type},
                    headers={"Accept": "application/json"},
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    orders = resp.json()
                    if orders:
                        price = (
                            min(o["price"] for o in orders)
                            if order_type == "sell"
                            else max(o["price"] for o in orders)
                        )
                        best_prices.setdefault(type_id, {})[key] = price
            except Exception as exc:
                print(f"[PRICE] fetch_type {type_id} {order_type} error: {exc}")

    async with httpx.AsyncClient() as client:
        for i in range(0, len(type_ids), 20):
            batch = type_ids[i : i + 20]
            await asyncio.gather(*[fetch_type(client, tid) for tid in batch])
            if i % 100 == 0 and i > 0:
                print(f"[PRICE] Progress: {i}/{len(type_ids)} types processed")

    print(f"[PRICE] Got prices for {len(best_prices)} types, saving to DB…")
    if not best_prices:
        return 0

    # Upsert into market_prices — preserving adjusted/average from existing rows
    for type_id, prices in best_prices.items():
        stmt = (
            sqlite_insert(MarketPrice)
            .values(
                type_id=type_id,
                buy_price=prices.get("buy_price"),
                sell_price=prices.get("sell_price"),
                last_updated=datetime.utcnow(),
            )
            .on_conflict_do_update(
                index_elements=["type_id"],
                set_={
                    "buy_price": prices.get("buy_price"),
                    "sell_price": prices.get("sell_price"),
                    "last_updated": datetime.utcnow(),
                },
            )
        )
        await db.execute(stmt)

    await db.commit()
    print(f"[PRICE] Done — saved {len(best_prices)} prices")
    return len(best_prices)


# ---------------------------------------------------------------------------
# Industry cost indexes (global, no auth required)
# ---------------------------------------------------------------------------


async def sync_industry_cost_indexes(db: AsyncSession) -> int:
    import httpx
    from app.models.sde import SdeSolarSystem

    esi = ESIClient(db)
    try:
        systems = await esi.get("/industry/systems/")
    finally:
        await esi.close()

    await db.execute(delete(IndustryCostIndex))

    system_ids: list[int] = []
    count = 0
    for system in systems:
        sys_id = system["solar_system_id"]
        system_ids.append(sys_id)
        for index in system.get("cost_indices", []):
            db.add(
                IndustryCostIndex(
                    solar_system_id=sys_id,
                    activity=index["activity"],
                    cost_index=index["cost_index"],
                    last_updated=datetime.utcnow(),
                )
            )
            count += 1

    await db.commit()

    # Resolve system IDs → names via the unauthenticated ESI universe/names endpoint
    await db.execute(delete(SdeSolarSystem))
    BATCH = 1000
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(system_ids), BATCH):
            batch = system_ids[i : i + BATCH]
            resp = await client.post(
                "https://esi.evetech.net/latest/universe/names/",
                json=batch,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                for item in resp.json():
                    if item.get("category") == "solar_system":
                        db.add(SdeSolarSystem(
                            solar_system_id=item["id"],
                            name=item["name"],
                        ))
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
    if not missing_set:
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
    # (diagnostic prints removed — 403s on private structures are expected and silent)

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
            solar_system_id: int | None = None
            non_403_errs: list[str] = []
            for char_id in all_char_ids:
                esi = ESIClient(db)
                try:
                    data = await esi.get(
                        f"/universe/structures/{struct_id}/",
                        character_id=char_id,
                    )
                    name = data.get("name")
                    solar_system_id = data.get("solar_system_id")
                    if name:
                        break
                except Exception as exc:
                    exc_str = str(exc)
                    if "403" not in exc_str:
                        # 403 = no docking access — expected and silent.
                        # Log anything else (5xx, network errors, etc.).
                        non_403_errs.append(f"char {char_id}: {exc_str[:120]}")
                finally:
                    await esi.close()

            if not name:
                if non_403_errs:
                    # Real errors (5xx, network, etc.) — surface these
                    struct_errors[struct_id] = non_403_errs
                    print(f"[LOC] structure {struct_id} unresolved — unexpected errors: {non_403_errs}")
                # 403-only: no access to that structure — silent, placeholder stored

            db.add(Location(
                location_id=struct_id,
                name=name or f"Unknown Structure ({struct_id})",
                location_type="structure",
                solar_system_id=solar_system_id,
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
# Resolve blueprint locations → solar_system_id
# ---------------------------------------------------------------------------


async def sync_blueprint_locations(db: AsyncSession) -> int:
    """
    For every unique location_id referenced in the blueprints table, look up
    the solar_system_id and cache it in the Location table.

    - NPC stations (id < 1e12): GET /universe/stations/{id}/ → system_id  (no auth)
    - Player structures (id ≥ 1e12): already handled by resolve_locations;
      solar_system_id should already be set from the structure response.

    Returns count of new/updated location rows.
    """
    import asyncio
    import httpx

    from app.models.location import Location

    # All unique location_ids used by blueprints
    bp_locs = [
        r[0]
        for r in (await db.execute(select(Blueprint.location_id).distinct())).fetchall()
    ]
    if not bp_locs:
        return 0

    # Check which ones already have solar_system_id resolved
    existing = {
        r[0]: r[1]
        for r in (
            await db.execute(
                select(Location.location_id, Location.solar_system_id)
                .where(Location.location_id.in_(bp_locs))
            )
        ).fetchall()
    }

    # Only need to fetch NPC stations (id < 1e12) without a solar_system_id yet
    to_fetch = [
        loc_id for loc_id in bp_locs
        if loc_id < 1_000_000_000_000 and existing.get(loc_id) is None
    ]
    if not to_fetch:
        return 0

    count = 0

    async def fetch_station(client: httpx.AsyncClient, station_id: int) -> None:
        nonlocal count
        try:
            resp = await client.get(
                f"https://esi.evetech.net/latest/universe/stations/{station_id}/",
                headers={"Accept": "application/json"},
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                sys_id = data.get("system_id")
                name = data.get("name", f"Station {station_id}")
                # Upsert into Location
                loc = await db.get(Location, station_id)
                if loc:
                    loc.solar_system_id = sys_id
                    loc.name = name
                    loc.location_type = "station"
                else:
                    db.add(Location(
                        location_id=station_id,
                        name=name,
                        location_type="station",
                        solar_system_id=sys_id,
                        last_updated=datetime.utcnow(),
                    ))
                count += 1
        except Exception as exc:
            print(f"[BPLOC] station {station_id} lookup failed: {exc}")

    async with httpx.AsyncClient() as client:
        # Concurrently fetch up to 20 at a time
        for i in range(0, len(to_fetch), 20):
            batch = to_fetch[i : i + 20]
            await asyncio.gather(*[fetch_station(client, sid) for sid in batch])

    if count:
        await db.commit()
    return count


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

    # Resolve blueprint station locations → solar_system_id for cost index lookup
    try:
        bp_locs = await sync_blueprint_locations(db)
        if bp_locs:
            results["blueprint_locations"] = bp_locs
    except Exception as exc:
        errors["blueprint_locations"] = str(exc)

    # Update last_synced timestamp
    result = await db.execute(select(Character).where(Character.character_id == character_id))
    char = result.scalar_one_or_none()
    if char:
        char.last_synced = datetime.utcnow()
        await db.commit()

    return {"synced": results, "errors": errors}

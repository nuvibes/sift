# Rule tests for access.yml, run by `semgrep --test`. Not application code.
#
# Every query here is parameterized, so nothing in this file is an injection example: the point
# is only *which table* is being read, and from where.
#
# ruff: noqa
# mypy: ignore-errors


# --- sift-no-asset-sql-outside-kernel ----------------------------------------------


async def reads_assets_directly(conn, asset_id, folder_id, root_id):
    # ruleid: sift-no-asset-sql-outside-kernel
    await conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))

    # The permissions themselves are a permission-carrying table: a slice that could write them
    # could grant itself anything.
    # ruleid: sift-no-asset-sql-outside-kernel
    await conn.execute("INSERT INTO acl_grants (id, effect) VALUES (?, 'share')", (asset_id,))

    # ruleid: sift-no-asset-sql-outside-kernel
    await conn.execute("SELECT * FROM asset_locations WHERE asset_id = ?", (asset_id,))

    # ruleid: sift-no-asset-sql-outside-kernel
    await conn.execute("SELECT id, name FROM folders WHERE root_id = ?", (root_id,))

    # ruleid: sift-no-asset-sql-outside-kernel
    await conn.execute("SELECT abs_path FROM library_roots WHERE id = ?", (root_id,))

    # ruleid: sift-no-asset-sql-outside-kernel
    await conn.execute("SELECT rel_cache_path FROM derivatives WHERE asset_id = ?", (asset_id,))

    # A join is how the check gets skipped without anyone noticing: the table in the FROM is an
    # innocent one, and the permission-carrying one is further along the line.
    # ruleid: sift-no-asset-sql-outside-kernel
    join = "SELECT t.name FROM asset_tags t JOIN assets a ON a.id = t.asset_id WHERE a.id = ?"
    await conn.execute(join, (asset_id,))

    # Writing is no better than reading.
    # ruleid: sift-no-asset-sql-outside-kernel
    await conn.execute("UPDATE assets SET vault = 1 WHERE id = ?", (asset_id,))

    # ruleid: sift-no-asset-sql-outside-kernel
    await conn.execute("INSERT INTO folders (id, root_id) VALUES (?, ?)", (folder_id, root_id))


async def goes_through_the_kernel(store, access, viewer, asset_id):
    # ok: sift-no-asset-sql-outside-kernel
    asset = await access.asset(viewer, asset_id)

    # ok: sift-no-asset-sql-outside-kernel
    locations = await store.locations(asset_id)

    # A feature's own tables are its own business. Only the ones carrying the access rules are
    # off limits, so a rule that flagged every table would be a rule nobody could work with.
    # ok: sift-no-asset-sql-outside-kernel
    await store._db.fetch_all("SELECT * FROM tags WHERE name = ?", ("holiday",))

    # ok: sift-no-asset-sql-outside-kernel
    await store._db.fetch_all("SELECT * FROM jobs WHERE state = 'queued'")

    return asset, locations


# --- sift-no-content-store-outside-kernel ------------------------------------------


async def serves_a_file(request, settings, viewer, asset_id):
    # ruleid: sift-no-content-store-outside-kernel
    store = request.app.state.content

    # ruleid: sift-no-content-store-outside-kernel
    ContentStore(request.app.state.database, settings)

    # The scoped way: the access layer resolves the path behind the permission check.
    # ok: sift-no-content-store-outside-kernel
    path = await request.app.state.access.locate(viewer, asset_id)

    # ok: sift-no-content-store-outside-kernel
    asset = await request.app.state.access.get_asset(viewer, asset_id)

    return store, path, asset


# --- sift-no-viewer-forgery-outside-kernel -----------------------------------------


async def who_is_asking(request, user_id):
    # ruleid: sift-no-viewer-forgery-outside-kernel
    forged = Viewer(id="anyone", role=Role.ADMIN)

    # The sanctioned viewer: read from the database, disabled accounts refused, role is whatever
    # the row says.
    # ok: sift-no-viewer-forgery-outside-kernel
    real = await request.app.state.access.load_viewer(user_id)

    return forged, real

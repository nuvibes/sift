# SPDX-License-Identifier: AGPL-3.0-or-later
"""The indexes of the tables `schema` makes, and of the grants."""

from __future__ import annotations

from sift.kernel.access import schema_columns

_INDEX_TAG_PARENT = "CREATE INDEX IF NOT EXISTS ix_tags_parent ON tags(parent_id)"

_ACCESS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_acl_subject ON acl_grants(subject_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_acl_object ON acl_grants(object_type, object_id)",
    # A global grant's `object_id` is NULL and SQLite counts NULLs as distinct, so this partial
    # unique index stops a second one.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_acl_global "
    "ON acl_grants(object_type, subject_user_id, effect) WHERE object_id IS NULL",
)

_SONG_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_songs_recording"
    " ON songs(recording_id) WHERE recording_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_songs_name ON songs(name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_song_files_song ON song_files(song_id, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_song_user_state_favorite"
    " ON song_user_state(user_id, favorite) WHERE favorite = 1",
)

_CATALOG_82_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_song_user_state_hidden"
    " ON song_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_artists_name ON artists(name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_song_artists_artist ON song_artists(artist_id, song_id)",
)


_CATALOG_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_asset_links_asset ON asset_links(asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_asset_people_person ON asset_people(person_id)",
    "CREATE INDEX IF NOT EXISTS ix_asset_people_source_decided ON asset_people(source, decided_at)",
    "CREATE INDEX IF NOT EXISTS ix_asset_person_refusals_person ON asset_person_refusals(person_id)",
    "CREATE INDEX IF NOT EXISTS ix_asset_tags_tag ON asset_tags(tag_id)",
    # The files of one post, partial: most filings have no post.
    "CREATE INDEX IF NOT EXISTS ix_asset_usernames_post"
    " ON asset_usernames(username_id, post_id, asset_id) WHERE post_id IS NOT NULL",
    # What one of Sift's passes filed, for one username, sorted so a capped read stops early.
    "CREATE INDEX IF NOT EXISTS ix_asset_usernames_source"
    " ON asset_usernames(source, username_id, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_asset_usernames_username ON asset_usernames(username_id)",
    "CREATE INDEX IF NOT EXISTS ix_collection_items_asset ON collection_items(asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_collection_tags_tag ON collection_tags(tag_id)",
    "CREATE INDEX IF NOT EXISTS ix_cus_favorite"
    " ON collection_user_state(user_id, favorite) WHERE favorite = 1",
    "CREATE INDEX IF NOT EXISTS ix_cus_hidden"
    " ON collection_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE INDEX IF NOT EXISTS ix_enrichment_runs_at ON enrichment_runs(subject, at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_enrichment_runs_subject"
    " ON enrichment_runs(subject, local_id, box_id, at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_loop_tags_tag ON loop_tags(tag_id)",
    "CREATE INDEX IF NOT EXISTS ix_loops_asset ON loops(asset_id, start_ms)",
    # Partial on the one value anybody asks for.
    "CREATE INDEX IF NOT EXISTS ix_people_kept_local ON people(id) WHERE keep_local = 1",
    schema_columns.INDEX_PEOPLE_KEPT_FROM_SWAPS,
    "CREATE INDEX IF NOT EXISTS ix_people_aliases_alias ON people_aliases(alias COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_people_aliases_person ON people_aliases(person_id)",
    "CREATE INDEX IF NOT EXISTS ix_people_links_person ON people_links(person_id)",
    "CREATE INDEX IF NOT EXISTS ix_people_links_site ON people_links(site_id)",
    "CREATE INDEX IF NOT EXISTS ix_people_links_url ON people_links(url)",
    "CREATE INDEX IF NOT EXISTS ix_person_tags_tag ON person_tags(tag_id)",
    # Partial: the rows nobody hearted are none of that query.
    "CREATE INDEX IF NOT EXISTS ix_pus_favorite"
    " ON person_user_state(user_id, favorite) WHERE favorite = 1",
    "CREATE INDEX IF NOT EXISTS ix_pus_hidden"
    " ON person_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE INDEX IF NOT EXISTS ix_psi_asset ON photo_set_items(asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_psi_set ON photo_set_items(photo_set_id, position)",
    "CREATE INDEX IF NOT EXISTS ix_pst_tag ON photo_set_tags(tag_id)",
    "CREATE INDEX IF NOT EXISTS ix_psus_hidden"
    " ON photo_set_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_photo_sets_archive"
    " ON photo_sets(archive_root_id, archive_rel_path) WHERE archive_rel_path IS NOT NULL",
    # One set per folder, enforced here rather than only by the service's lookup.
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_photo_sets_folder"
    " ON photo_sets(folder_id) WHERE folder_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_site_aliases_alias ON site_aliases(alias COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_site_aliases_site ON site_aliases(site_id)",
    "CREATE INDEX IF NOT EXISTS ix_site_links_site ON site_links(site_id)",
    "CREATE INDEX IF NOT EXISTS ix_site_tags_tag ON site_tags(tag_id)",
    "CREATE INDEX IF NOT EXISTS ix_site_user_state_favorite ON site_user_state(user_id, favorite)",
    "CREATE INDEX IF NOT EXISTS ix_site_user_state_hidden ON site_user_state(user_id, hidden)",
    "CREATE INDEX IF NOT EXISTS ix_sites_kept_local ON sites(id) WHERE keep_local = 1",
    schema_columns.INDEX_SITES_KEPT_FROM_SWAPS,
    "CREATE INDEX IF NOT EXISTS ix_sites_parent ON sites(parent_id)",
    "CREATE INDEX IF NOT EXISTS ix_tag_aliases_alias ON tag_aliases(alias COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_tag_aliases_tag ON tag_aliases(tag_id)",
    "CREATE INDEX IF NOT EXISTS ix_tus_favorite"
    " ON tag_user_state(user_id, favorite) WHERE favorite = 1",
    "CREATE INDEX IF NOT EXISTS ix_tus_hidden ON tag_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE INDEX IF NOT EXISTS ix_tags_kept_local ON tags(id) WHERE keep_local = 1",
    schema_columns.INDEX_TAGS_KEPT_FROM_SWAPS,
    _INDEX_TAG_PARENT,
    # NOCASE, matching how the name is compared, or the query cannot use it.
    "CREATE INDEX IF NOT EXISTS ix_usernames_name ON usernames(name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_usernames_person ON usernames(person_id)",
    "CREATE INDEX IF NOT EXISTS ix_usernames_site ON usernames(site_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_usernames_number"
    " ON usernames(site_id, number) WHERE number IS NOT NULL",
)

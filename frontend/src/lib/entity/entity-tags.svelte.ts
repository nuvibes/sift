/* The tags on one person, or one site. Its own tiny store rather than a field on the People
 * store, because it is fetched per detail page and thrown away with it. */

import { api } from '$lib/api/client';
import { toasts } from '$lib/shell/toasts.svelte';
import { tags as tagStore } from '$lib/entity/tags.svelte';
import { pickRow } from '$lib/entity/entity-picture';
import type { VerbPick } from '$lib/components/common/verbs';
import type { components } from '$lib/api/schema';
import { counted } from '$lib/entity/entity-counts';

/* A tag as any of the four kinds of thing carries one. */
type EntityTag =
	| components['schemas']['TagOnEntity']
	| components['schemas']['TagOnCollection']
	| components['schemas']['TagOnPhotoSet'];

export type EntityKind = 'people' | 'sites' | 'collections' | 'photo-sets';

class EntityTags {
	/** The tags on whatever was last asked for. */
	items = $state<EntityTag[]>([]);

	/* Which request the rows belong to. */
	#generation = 0;

	async load(kind: EntityKind, id: string): Promise<void> {
		const mine = ++this.#generation;
		try {
			const found = await api.get<EntityTag[]>(`/${kind}/${id}/tags`);
			if (mine !== this.#generation) return;
			this.items = found;
		} catch {
			// An empty list rather than an error: the page is still readable without them, and a
			// screen that refuses to draw because a secondary fetch failed is worse than one
			// missing a row of chips.
			if (mine === this.#generation) this.items = [];
		}
	}

	/** Put a tag on, or take it off. The server answers with the whole set, which is what is kept:
	 * a client that patched its own copy would drift from what was really stored. */
	async set(kind: EntityKind, id: string, tagId: string, add: boolean): Promise<void> {
		const mine = ++this.#generation;
		const held = await api.post<EntityTag[]>(`/${kind}/${id}/tags`, {
			body: { tag_id: tagId, add }
		});
		if (mine !== this.#generation) return;
		this.items = held;
	}

	/** Between two detail pages, so the next one does not draw the last one's chips while it waits. */
	forget(): void {
		this.#generation += 1;
		this.items = [];
	}
}

export const entityTags = new EntityTags();

/** Put one tag on several things in one go, from a wall rather than from a detail page. */
export async function tagMany(
	kind: EntityKind,
	ids: readonly string[],
	tagIds: readonly string[],
	add = true
): Promise<void> {
	for (const id of ids) {
		for (const tagId of tagIds) {
			await api.post(`/${kind}/${id}/tags`, { body: { tag_id: tagId, add } });
		}
	}
}

/** The tag picker every wall of entities offers, written once. */
export function tagPickerFor(kind: EntityKind, many: string, done: () => void): VerbPick {
	return {
		kind: 'tag',
		plural: 'tags',
		/* A page of tags from the server, alphabetical, filtered by what is typed. */
		ask: async (typed: string) => {
			const asked = await tagStore.choices(typed);
			return {
				choices: asked.items.map((tag) => pickRow('tag', tag)),
				more: Math.max(0, asked.total - asked.items.length)
			};
		},
		/* Answers with what LANDED (`PickLanded`), so the picker puts its mark back on a
		   refusal. */
		pick: async (ids, choice) => {
			let went = 0;
			try {
				for (const id of ids) {
					await tagMany(kind, [id], [choice.id]);
					went += 1;
				}
				toasts.show(ids.length === 1 ? 'Tagged' : `Tagged ${counted(ids.length)} ${many}`);
				done();
				return 'landed';
			} catch {
				toasts.show("That tag couldn't be added", { tone: 'error' });
				return went === 0 ? 'refused' : 'partly';
			}
		},
		create: async (name: string) => {
			const made = await tagStore.create(name);
			return { id: made.id, name: made.name };
		}
	};
}

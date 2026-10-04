/* The tags on one person, or one site.
 *
 * Its own tiny store rather than a field on the People store, because it is fetched per detail
 * page and thrown away with it. Keeping it in the list store would mean either loading every
 * entity's tags to show one screen, or a cache that has to be invalidated from three places.
 *
 * The kind is part of the address rather than a flag the server reads: `/people/{id}/tags` and
 * `/sites/{id}/tags` are two endpoints, and the only thing this shares between them is the
 * shape of the answer.
 */

import { api } from '$lib/api/client';
import { toasts } from '$lib/shell/toasts.svelte';
import { tags as tagStore } from '$lib/entity/tags.svelte';
import { pickRow } from '$lib/entity/entity-picture';
import type { VerbPick } from '$lib/components/common/verbs';
import type { components } from '$lib/api/schema';
import { counted } from '$lib/entity/entity-counts';

/*
 * A tag as any of the four kinds of thing carries one.
 *
 * The server keeps a separate model per kind on purpose (see `TagOnEntity`), and this store
 * fetches from all four addresses, so it says all four rather than naming the one it happened to
 * be written against. They are the same three fields today, which is exactly why naming only one
 * of them would compile and go on compiling until the day one of them stopped being.
 */
type EntityTag =
	| components['schemas']['TagOnEntity']
	| components['schemas']['TagOnCollection']
	| components['schemas']['TagOnPhotoSet'];

export type EntityKind = 'people' | 'sites' | 'collections' | 'photo-sets';

class EntityTags {
	/** The tags on whatever was last asked for. */
	items = $state<EntityTag[]>([]);

	/* Which request the rows belong to.
	 *
	 * A detail page navigated away from has a fetch in the air, and its answer arrives after the
	 * next page has asked for its own, so without this the previous person's tags appear under
	 * this person's name, and taking one off there removes a tag from somebody else.
	 */
	#generation = 0;

	async load(kind: EntityKind, id: string): Promise<void> {
		const mine = ++this.#generation;
		try {
			const found = await api.get<EntityTag[]>(`/${kind}/${id}/tags`);
			if (mine !== this.#generation) return;
			this.items = found;
		} catch {
			// An empty list rather than an error: the page is still readable without them, and a
			// screen that refuses to draw because a secondary fetch failed is worse than one missing
			// a row of chips.
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

/**
 * Put one tag on several things at once, from a wall rather than from a detail page.
 *
 * Deliberately NOT a method on the store above. That store holds the tags of the one thing a
 * detail page is showing, and every write to it replaces that set with the server's answer. So a
 * bulk write through it would leave the chips of whichever row happened to answer last standing
 * under somebody else's name. This writes and reports; nothing on screen is a copy of it.
 *
 * One request per thing, because the endpoint is per thing. A failure on any of them fails the
 * whole call rather than being swallowed: a partial tagging that does not say which part worked is
 * worse than a refusal.
 */
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

/**
 * The tag picker every wall of entities offers, written once.
 *
 * Four walls carry the same Tag verb over the same four endpoints; one copy per wall would be a
 * wording that drifts and a fix that reaches three of them: the same reasoning the verb LIST is
 * declared once under, applied to the list behind one of those verbs.
 *
 * The bar and the menu both open out into it: one pick, written at once to everything picked.
 */
export function tagPickerFor(kind: EntityKind, many: string, done: () => void): VerbPick {
	return {
		kind: 'tag',
		plural: 'tags',
		/* A page of tags from the server, alphabetical, filtered by what is typed. Never the
		   store's cached wall, which is one page of an order chosen for a different screen. */
		ask: async (typed: string) => {
			const asked = await tagStore.choices(typed);
			return {
				choices: asked.items.map((tag) => pickRow('tag', tag)),
				more: Math.max(0, asked.total - asked.items.length)
			};
		},
		/* Answers with what LANDED (`PickLanded`), so the picker puts its mark back on a refusal.
		   One entity at a time (the order `tagMany` already writes in), counting the ones that
		   went, because this route answers per entity and throws on the first it cannot do: nothing
		   gone is refused, some gone is partly. */
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

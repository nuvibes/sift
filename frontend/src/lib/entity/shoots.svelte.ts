/* Proposed shoots: runs of one creator's loose pictures that look like one sitting.
 *
 * **Nothing here decides who may see anything.** Every picture arriving on a proposal has already
 * been resolved against whoever is asking: a file this account may not see is simply not in the
 * list, and a proposal it could not be shown all of answers as though it were not there. Working
 * any of that out again in the browser would be a second opinion about concealment, in the one
 * place it cannot be read.
 *
 * **Three separate answers, three requests, deliberately.** Making the Photo Set, refusing the
 * grouping and naming the pictures that carry nobody are three different decisions about the same
 * pictures, each with its own receipt on the decisions page. Folding them into one call would make
 * "yes, and while you are there" a thing somebody cannot take back by halves.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { asked, type PageAsk } from '$lib/grid/anchor';

export type Shoot = components['schemas']['ShootView'];
type ShootPicture = components['schemas']['ShootPictureView'];
type ShootPage = components['schemas']['ShootList'];
type Made = components['schemas']['MadeView'];
type Refused = components['schemas']['RefusedView'];
type Named = components['schemas']['NamedView'];

/** How many proposals a page asks for before the wall has measured a card. The server's own
 *  default, so the two agree; after that the page is as many whole rows as the screen holds. */
export const SHOOTS_PER_PAGE = 20;

/**
 * One page of the proposals: at an offset, or at the proposal the address names (`from`).
 *
 * `from` is resolved by the server against the same list the page reads, and a proposal no longer
 * on it answers the top (see the route). One argument rather than two, because the two ways of
 * saying where a page starts must not be sent together (`PageAsk`).
 */
export async function shoots(
	page: PageAsk = { limit: SHOOTS_PER_PAGE, offset: 0 }
): Promise<ShootPage> {
	return api.get<ShootPage>('/shoots', { query: asked(page) });
}

/** One proposal with every picture of it: the page a card on the Shoots wall opens. */
export async function shoot(id: string): Promise<Shoot> {
	return api.get<Shoot>(`/shoots/${encodeURIComponent(id)}`);
}

/**
 * Yes: these pictures are one sitting, so make the Photo Set.
 *
 * `name` is what "Create with a name..." typed; left out, the Photo Set takes the proposal's own
 * name (the creator's). Sent only when given, so the ordinary press is the same empty request the
 * board's card sends and the server has one reading of "no name" rather than two.
 */
export async function makeTheSet(id: string, name?: string): Promise<Made> {
	return api.post<Made>(
		`/shoots/${encodeURIComponent(id)}/make`,
		name === undefined ? {} : { body: { name } }
	);
}

/** Not a shoot. Remembered against the pictures, so no rearrangement of them comes back. */
export async function notASet(id: string): Promise<Refused> {
	return api.post<Refused>(`/shoots/${encodeURIComponent(id)}/refuse`, {});
}

/** Put the creator on the pictures of this shoot that carry nobody at all. */
export async function nameTheRest(id: string): Promise<Named> {
	return api.post<Named>(`/shoots/${encodeURIComponent(id)}/name-the-rest`, {});
}

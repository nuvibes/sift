/* Proposed shoots: runs of one creator's loose pictures that look like one sitting. */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { asked, type PageAsk } from '$lib/grid/anchor';

export type Shoot = components['schemas']['ShootView'];
type ShootPicture = components['schemas']['ShootPictureView'];
type ShootPage = components['schemas']['ShootList'];
type Made = components['schemas']['MadeView'];
type Refused = components['schemas']['RefusedView'];
type Named = components['schemas']['NamedView'];

/** How many proposals a page asks for before the wall has measured a card. */
export const SHOOTS_PER_PAGE = 20;

/** One page of the proposals: at an offset, or at the proposal the address names (`from`). */
export async function shoots(
	page: PageAsk = { limit: SHOOTS_PER_PAGE, offset: 0 }
): Promise<ShootPage> {
	return api.get<ShootPage>('/shoots', { query: asked(page) });
}

/** One proposal with every picture of it: the page a card on the Shoots wall opens. */
export async function shoot(id: string): Promise<Shoot> {
	return api.get<Shoot>(`/shoots/${encodeURIComponent(id)}`);
}

/** Yes: these pictures are one sitting, so make the Photo Set. */
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

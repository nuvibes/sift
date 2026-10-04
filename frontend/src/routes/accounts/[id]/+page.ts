import { redirect } from '@sveltejs/kit';

/*
 * An OLD ADDRESS, kept so a pasted link lands somewhere.
 *
 * A username has no page of its own: it is stored and is not a place. Its files are the Files wall
 * filtered to it (`/browse?username=<id>`), which is where every press on a username goes, so
 * that is where a link from a message, a bookmark or an old History line goes too. Without this the
 * address would draw nothing at all, which reads as a broken app rather than a moved page.
 *
 * Permanent (308): the old address will never mean anything else.
 *
 * Not through `$lib/shell/moved`, which maps an old FIRST SEGMENT to a new one (`/platforms/<id>` is
 * `/sites/<id>`); this is not that shape: the page went and the id becomes a filter on another
 * wall. The query is not carried: whatever the old page's own address held (a tab) means nothing on
 * the Files wall.
 */
export function load({ params }: { params: { id: string } }): never {
	redirect(308, `/browse?username=${encodeURIComponent(params.id)}`);
}

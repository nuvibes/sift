/* Where one item of a queue lives: the addresses a screen links to, and the two queue names they
 * are built from.
 *
 * Apart from `panels.ts` because that registry imports every panel and the panels link here: with
 * the addresses in the registry the two import each other, and a hot reload of a panel throws.
 * This module imports no component, so nothing that draws belongs here.
 */

/** The faces tab a pile of look-alike faces is opened under, whichever tab it was reached from. */
export const TO_NAME = 'faces-to-name';
/** The duplicates queue, whose one item is a chain of copies too long to answer in the queue. */
export const DUPLICATES = 'duplicates';

/** Where one chain lives. Named by the only name a group has. See `keyOf`. */
export function chainHref(key: string): string {
	return `/organize/${DUPLICATES}/${key}`;
}

/**
 * Where one pile of look-alike faces lives, for every surface that links to one.
 *
 * Both paths are written whole so the reachability check can read them in the source. The tab it
 * was opened from rides in the address so a refresh, a shared link and Back all return to it;
 * `organizeCrumbs` refuses a name that is not a tab. It is `via`, not `from`, because `from` is
 * the grid anchor's key and the first page turn would overwrite it.
 */
export function pileHref(pile: { id: string; status?: string | null }, from?: string): string {
	return from ? `/organize/${TO_NAME}/${pile.id}?via=${from}` : `/organize/${TO_NAME}/${pile.id}`;
}

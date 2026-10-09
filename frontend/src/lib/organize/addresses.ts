/* Where one item of a queue lives: the addresses a screen links to, and the two queue names they
 * are built from. */

/** The faces tab a pile of look-alike faces is opened under, whichever tab it was reached from. */
export const TO_NAME = 'faces-to-name';
/** The duplicates queue, whose one item is a chain of copies too long to answer in the queue. */
export const DUPLICATES = 'duplicates';

/** Where one chain lives. Named by the only name a group has. See `keyOf`. */
export function chainHref(key: string): string {
	return `/organize/${DUPLICATES}/${key}`;
}

/** Where one pile of look-alike faces lives, for every surface that links to one. */
export function pileHref(pile: { id: string; status?: string | null }, from?: string): string {
	return from ? `/organize/${TO_NAME}/${pile.id}?via=${from}` : `/organize/${TO_NAME}/${pile.id}`;
}

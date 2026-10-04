/**
 * A list in a random order, shuffled in place of a copy.
 *
 * Fisher-Yates rather than a comparator returning a random number: the latter is not a shuffle at
 * all: it asks an inconsistent question of a sort algorithm, and which orders come out depends on
 * the browser's sort rather than on chance.
 *
 * ONE SHUFFLE, TWO CALLERS. A Theater cell shuffles each page it fills, and the player's Shuffle
 * shuffles a list it holds whole (see `Walk` in `player/run.svelte.ts`). Two copies of one small
 * algorithm are the pair that drifts, so it is here and both read it.
 */
export function shuffled<T>(items: readonly T[]): T[] {
	const out = [...items];
	for (let at = out.length - 1; at > 0; at -= 1) {
		const swap = Math.floor(Math.random() * (at + 1));
		[out[at], out[swap]] = [out[swap], out[at]];
	}
	return out;
}

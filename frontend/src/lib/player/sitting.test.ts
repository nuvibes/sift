/*
 * What a screen REPORTS for a file that has no end of its own.
 *
 * The still's report is not the player's sitting report and the two are not the same mechanism. A
 * photograph is viewed by being OPENED, so its sitting goes in two pieces: an empty one on the
 * way in, which is what earns the view, and the time on the way out. **The second piece carries a
 * ZERO where the first would have carried null, and that zero is the whole of it**: it says a piece
 * of this sitting has already gone, which is what stops the second one being counted as a second
 * view.
 *
 * Facts, never conclusions. Whether something was a view is the server's judgement (three screens
 * send this report and a rule living in one of them is not a rule), so what is asserted here is
 * what the client SAYS.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';

/* Typed with the arguments it is called with rather than as a bare `vi.fn()`: a double declared
   with no parameters gives `mock.calls` the type `[][]`, and reading what was SENT is the only
   reason this file exists. */
const post = vi.fn(async (_path: string, _options?: unknown) => ({}) as unknown);
vi.mock('$lib/api/client', () => ({
	api: {
		post: (path: string, options?: unknown) => post(path, options),
		get: vi.fn(async () => ({})),
		put: vi.fn(async () => ({}))
	}
}));

const { handOffSitting, inTheCorner, newSitting, newSittingId, noteMagnified } =
	await import('./sitting.svelte');

/** Where these sittings happen. Every screen says; see the test at the foot of this file. */
const PANEL = { screen: 'panel', opened_from: 'library' } as const;

let clock = 0;

beforeEach(() => {
	vi.clearAllMocks();
	/* NOT zero, for the reason the player's own view tests give: a first stamp of nought reads as no
	   time at all in anything that guards on it, and a fake clock starting there measures a sitting
	   of nought while every assertion still passes. */
	clock = 1_000;
	vi.spyOn(performance, 'now').mockImplementation(() => clock);
});

afterEach(() => vi.restoreAllMocks());

/** Let every request in the air come back, which in a browser happens between one press and the
 *  next. Load-bearing: the put-back after a failed opening report runs in a `catch`. */
async function settle() {
	for (let turn = 0; turn < 4; turn++) await Promise.resolve();
}

/** Every report, in order, as `[path, body]`. */
function reports() {
	return post.mock.calls.map(
		(call) => [String(call[0]), (call[1] as { body: Record<string, unknown> }).body] as const
	);
}

it('earns the view on the way in, with no time on it', async () => {
	const sitting = newSitting();

	sitting.start('asset-1', PANEL);
	await settle();

	const [path, body] = reports()[0];
	expect(path).toBe('/assets/asset-1/view');
	// A picture is viewed by being opened. The server keeps that rule and does not wait to find out.
	expect(body.watch_ms).toBe(0);
	expect(body.already_reported_ms).toBeNull();
});

it('reports the time on the way out, saying a piece has already gone', async () => {
	const sitting = newSitting();

	sitting.start('asset-1', PANEL);
	await settle();
	clock += 4_000;
	sitting.end();
	await settle();

	const [, body] = reports()[1];
	expect(body.watch_ms).toBe(4_000);
	/* ZERO, NOT NULL, and that is the assertion this file exists for. Null would tell the server
	   "judge this fresh", which is what makes a second playthrough a second view: said here it
	   would count one look at one picture twice. */
	expect(body.already_reported_ms).toBe(0);
	expect(body.ended).toBe(false);
	// A still is looked at rather than played, so there is no position to keep, and zero is what
	// clears a resume point left on that file by something else.
	expect(body.position_ms).toBe(0);
});

it('says nothing at all when there was no sitting to end', () => {
	const sitting = newSitting();

	sitting.end();

	expect(reports()).toHaveLength(0);
});

it('finishes the previous picture off before starting the next', async () => {
	/* Stepping from one still to the next fires no unmount (the frame lives across the change),
	   so without this the previous file's time is simply dropped. */
	const sitting = newSitting();

	sitting.start('asset-1', PANEL);
	await settle();
	clock += 2_000;
	sitting.start('asset-2', PANEL);
	await settle();

	const sent = reports();
	expect(sent.map(([path]) => path)).toEqual([
		'/assets/asset-1/view',
		'/assets/asset-1/view',
		'/assets/asset-2/view'
	]);
	expect(sent[1][1].watch_ms).toBe(2_000);
	expect(sitting.on).toBe('asset-2');
});

it('carries the whole sitting when the opening report never landed', async () => {
	/* The honest way to fail. Nothing was counted, so the report on the way out asks for the sitting
	   to be judged fresh and the view is counted then instead. One view lost to a request that
	   failed is the right side to be wrong on; the other side counts it twice. */
	const sitting = newSitting();

	post.mockRejectedValueOnce(new Error('the network went'));
	sitting.start('asset-1', PANEL);
	await settle();
	clock += 3_000;
	sitting.end();
	await settle();

	const [, body] = reports()[1];
	expect(body.watch_ms).toBe(3_000);
	expect(body.already_reported_ms).toBeNull();
});

it('does not correct a file that has already been left', async () => {
	/*
	 * The guard inside the `catch`, and it is not tidying.
	 *
	 * A failed opening report arrives back after the person has stepped on. Put back without asking
	 * WHICH file it was about, it clears the piece the NEXT picture has already had counted, so
	 * that one's closing report says "judge this fresh" over a sitting the server has already seen
	 * the start of, and one look at it becomes two views.
	 */
	const sitting = newSitting();

	post.mockRejectedValueOnce(new Error('the network went'));
	sitting.start('asset-1', PANEL);
	// Stepped on BEFORE the failure lands, which is the ordinary case at the speed a key repeats.
	sitting.start('asset-2', PANEL);
	await settle();
	clock += 1_000;
	sitting.end();
	await settle();

	const closing = reports()[reports().length - 1];
	expect(closing[0]).toBe('/assets/asset-2/view');
	expect(
		closing[1].already_reported_ms,
		"the first file's failure cleared the second file's opening piece"
	).toBe(0);
});

it('gives every screen its own record', async () => {
	/* Two of these views can be up together (the corner panel and a full-size view behind it), and
	   a shared record would have the second one end the first one's sitting on the way in. */
	const corner = newSitting();
	const full = newSitting();

	corner.start('asset-1', PANEL);
	full.start('asset-2', PANEL);
	await settle();

	expect(corner.on).toBe('asset-1');
	expect(full.on).toBe('asset-2');
	expect(reports().map(([path]) => path)).toEqual(['/assets/asset-1/view', '/assets/asset-2/view']);
});

it('names the sitting, and both pieces carry the same name', async () => {
	/* The whole reason the id exists. A picture reports twice (an empty piece when it is opened and
	   the time when it is left), and without a name on them the two would be two rows in `plays`,
	   one of them always zero-length: every picture opened would read as two sittings. */
	const sitting = newSitting();

	sitting.start('asset-1', PANEL);
	await settle();
	clock += 4_000;
	sitting.end();
	await settle();

	const [opening, leaving] = reports().map(([, body]) => body);
	expect(opening.sitting).toEqual(expect.any(String));
	expect(String(opening.sitting).length).toBeGreaterThan(16);
	expect(leaving.sitting).toBe(opening.sitting);
});

it('gives the next file a name of its own', async () => {
	/* The other direction, and the one a shared id would break silently: two pictures looked at one
	   after the other are two sittings, and the server folds by the name. */
	const sitting = newSitting();

	sitting.start('asset-1', PANEL);
	await settle();
	sitting.start('asset-2', PANEL);
	await settle();

	const names = reports().map(([, body]) => body.sitting);
	expect(new Set(names).size).toBe(2);
});

it('mints its name without the API that plain http does not have', async () => {
	/* `crypto.randomUUID` is the obvious call and is unavailable here: Sift is served over plain http
	   on a home network by design, which is not a secure context, and the method is simply not
	   defined there. Asserted by taking it away. */
	const withoutIt = { getRandomValues: crypto.getRandomValues.bind(crypto) } as Crypto;
	vi.spyOn(globalThis, 'crypto', 'get').mockReturnValue(withoutIt);

	expect(newSittingId()).toMatch(/^[0-9a-f]{32}$/);
});

it('says where it happened on both pieces, so the server keeps it whichever lands', async () => {
	/* The place is a fact about the sitting's beginning, and the server writes it from the first
	   piece it receives. Either piece can be the first to arrive (the opening one can be lost),
	   so both carry it, and the opening piece is not trusted to be the one that lands. */
	const sitting = newSitting();
	const place = {
		screen: 'panel',
		opened_from: 'loops',
		loop: 'a-saved-loop',
		kept_filter: null
	} as const;

	sitting.start('asset-1', place);
	await settle();
	clock += 2_000;
	sitting.end();
	await settle();

	for (const [, body] of reports()) {
		expect(body).toMatchObject(place);
	}
	expect(reports()).toHaveLength(2);
});

it('hands a picture to the corner as ONE sitting, reported once on the way out', async () => {
	/* A picture moved from the panel to the corner is still being looked at. A fresh sitting there
	   would count a second view; no sitting there wrote nothing for the time in the corner. */
	/* Ids of their own: a sitting another test began and never ended is still in progress. */
	const panel = newSitting();
	const corner = newSitting();

	panel.start('handed-1', PANEL);
	await settle();
	clock += 3_000;
	const baton = handOffSitting('handed-1', 'panel');
	expect(baton).not.toBeNull();
	corner.resume(baton!);
	// The panel going away afterwards says nothing: its sitting is the corner's now.
	panel.end();
	await settle();
	expect(reports()).toHaveLength(1);

	clock += 5_000;
	corner.end();
	await settle();

	const [opening, leaving] = reports().map(([, body]) => body);
	expect(reports()).toHaveLength(2);
	expect(leaving.sitting).toBe(opening.sitting);
	expect(leaving.watch_ms).toBe(8_000);
	expect(leaving.already_reported_ms).toBe(0);
	expect(leaving).toMatchObject(PANEL);
});

it('hands over nothing for a file the screen is not sitting with', () => {
	const panel = newSitting();
	panel.start('handed-1', PANEL);

	expect(handOffSitting('handed-2', 'panel')).toBeNull();
	expect(handOffSitting('handed-1', 'corner')).toBeNull();
	expect(panel.on).toBe('handed-1');
	panel.end();
});

it('says on the way out whether the picture was magnified, and false is said too', async () => {
	const looked = newSitting();
	const magnified = newSitting();
	looked.start('asset-1', PANEL);
	magnified.start('asset-2', PANEL);

	noteMagnified('asset-2');
	looked.end();
	magnified.end();
	await settle();

	const closing = reports().filter(
		([, body]) => body.watch_ms !== 0 || body.already_reported_ms === 0
	);
	expect(closing.map(([path, body]) => [path, body.magnified])).toEqual([
		['/assets/asset-1/view', false],
		['/assets/asset-2/view', true]
	]);
	// No time filled the screen here, and that is a measurement, not an absence.
	expect(closing.every(([, body]) => body.fullscreen_ms === 0)).toBe(true);
});

it('carries the magnifying across a hand-over to the corner', async () => {
	const panel = newSitting();
	const corner = newSitting();
	panel.start('asset-1', PANEL);
	noteMagnified('asset-1');

	const baton = handOffSitting('asset-1', 'panel');
	expect(baton?.magnified).toBe(true);
	corner.resume(baton!);
	corner.end();
	await settle();

	expect(reports().at(-1)?.[1].magnified).toBe(true);
});

it('says a file carried on in the corner was opened from what the panel was opened from', () => {
	const from = {
		screen: 'panel',
		opened_from: 'person',
		opened_from_id: '01HX0000000000000000000901',
		loop: null,
		kept_filter: null
	} as const;

	expect(inTheCorner(from)).toMatchObject({
		screen: 'corner',
		opened_from: 'person',
		opened_from_id: '01HX0000000000000000000901'
	});
	expect(inTheCorner(null)).toEqual({ screen: 'corner' });
});

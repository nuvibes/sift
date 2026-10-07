import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, type Page } from '@playwright/test';

/*
 * Seeding through the real API, and taking it away again.
 *
 * The e2e server starts on an empty temporary library (`serve.mjs`), so a journey that needs people
 * or collections makes them itself, through the same endpoints the screens use, and removes them in
 * its own teardown. Nothing here writes to a database directly: a row the API would refuse is a row
 * no user could have, and a journey proven over it proves nothing.
 */

/** The CSRF token this browser's session must echo on a state-changing request. */
export async function csrfOf(page: Page): Promise<string> {
	const me = await page.request.get('/api/auth/me');
	expect(me.ok(), 'not signed in').toBeTruthy();
	return String((await me.json()).csrf_token);
}

/** Make `count` people named `<stem>-<nnn>` (zero-padded so the names sort as numbers do). */
export async function seedPeople(page: Page, stem: string, count: number): Promise<string[]> {
	const token = await csrfOf(page);
	const ids: string[] = [];
	for (let at = 0; at < count; at += 1) {
		const made = await page.request.post('/api/people', {
			data: { name: `${stem}-${String(at).padStart(3, '0')}`, vault: false },
			headers: { 'x-csrf-token': token }
		});
		expect(made.ok(), `could not seed a person: ${await made.text()}`).toBeTruthy();
		ids.push(String((await made.json()).id));
	}
	return ids;
}

/** Remove people this spec made. A 404 is fine: the journey may have removed one itself. */
export async function removePeople(page: Page, ids: string[]): Promise<void> {
	const token = await csrfOf(page);
	for (const id of ids) {
		const gone = await page.request.delete(`/api/people/${id}`, {
			headers: { 'x-csrf-token': token }
		});
		expect([204, 404], `could not remove a seeded person ${id}`).toContain(gone.status());
	}
}

/** Make one collection and return its id. */
export async function seedCollection(page: Page, name: string): Promise<string> {
	const made = await page.request.post('/api/collections', {
		data: { name },
		headers: { 'x-csrf-token': await csrfOf(page) }
	});
	expect(made.ok(), `could not seed a collection: ${await made.text()}`).toBeTruthy();
	return String((await made.json()).id);
}

/** Remove collections this spec made. A 404 is fine: the journey may have removed one itself. */
export async function removeCollections(page: Page, ids: string[]): Promise<void> {
	const token = await csrfOf(page);
	for (const id of ids) {
		const gone = await page.request.delete(`/api/collections/${id}`, {
			headers: { 'x-csrf-token': token }
		});
		expect([204, 404], `could not remove a seeded collection ${id}`).toContain(gone.status());
	}
}

/**
 * Write keys of this account's interface state (`/api/settings/interface`), the document the rail
 * order and the pickers' recent picks live in. Written as the client writes them: each value a
 * string, JSON where the key holds a list.
 */
export async function writeInterfaceState(
	page: Page,
	state: Record<string, string>
): Promise<void> {
	const put = await page.request.put('/api/settings/interface', {
		data: { state },
		headers: { 'x-csrf-token': await csrfOf(page) }
	});
	expect(put.ok(), `could not write the interface state: ${await put.text()}`).toBeTruthy();
}

/*
 * REAL FILES, for a journey that needs the server to hold a file: its history, its people.
 *
 * Written into the media area `serve.mjs` points the folder picker at (`frontend/e2e/.media`, the
 * `MEDIA` of `library.spec.ts`), added as a library folder through the API with its scan on, and
 * waited for until the files are in the library. Each copy of the fixture photo gets a distinct
 * tail after the image data, so the copies are distinct files to the library rather than one file
 * found several times; a decoder stops at the image's end marker and never reads the tail.
 */
const MEDIA = fileURLToPath(new URL('./.media', import.meta.url));
const PHOTO = fileURLToPath(
	new URL('../../src/sift/kernel/tests/fixtures/ingress/accepted.jpg', import.meta.url)
);

export interface SeededFolder {
	rootId: string;
	directory: string;
	assetIds: string[];
}

export async function seedPhotos(page: Page, folder: string, count: number): Promise<SeededFolder> {
	const directory = join(MEDIA, folder);
	mkdirSync(directory, { recursive: true });
	const photo = readFileSync(PHOTO);
	for (let at = 0; at < count; at += 1) {
		const tail = Buffer.from(`${folder}-${at}-${'x'.repeat(64 * (at + 1))}`);
		writeFileSync(
			join(directory, `photo-${String(at).padStart(2, '0')}.jpg`),
			Buffer.concat([photo, tail])
		);
	}
	const added = await page.request.post('/api/library/roots', {
		data: { abs_path: directory },
		headers: { 'x-csrf-token': await csrfOf(page) }
	});
	expect(added.ok(), `could not add the folder: ${await added.text()}`).toBeTruthy();
	const rootId = String((await added.json()).id);
	await skipTheFirstFolderBenchmark(page);

	let assetIds: string[] = [];
	await expect
		.poll(
			async () => {
				const answer = await page.request.get('/api/assets?limit=200');
				if (!answer.ok()) return -1;
				const items = ((await answer.json()).items ?? []) as { id: string }[];
				assetIds = items.map((item) => item.id);
				return assetIds.length;
			},
			{ timeout: 60_000, message: 'the seeded photos never reached the library' }
		)
		.toBeGreaterThanOrEqual(count);
	return { rootId, directory, assetIds };
}

/**
 * Stop the benchmark a first library folder starts, so the folder is read immediately.
 *
 * The e2e server is a device nobody has measured, so the first folder added to its library queues
 * the benchmark and holds the folder's scan behind it for minutes. A journey about files is not
 * about that, so it does what a person may do on Activity: stop the run, which lets the scan go.
 * The Python suite declines the same reaction (`no_benchmark_on_a_first_folder`).
 */
export async function skipTheFirstFolderBenchmark(page: Page): Promise<void> {
	const answer = await page.request.get('/api/jobs?limit=50');
	expect(answer.ok(), `could not read the queue: ${await answer.text()}`).toBeTruthy();
	const jobs = ((await answer.json()).jobs ?? []) as { id: string; type: string; state: string }[];
	const held = jobs.filter(
		(job) =>
			job.type === 'performance_benchmark' && (job.state === 'queued' || job.state === 'running')
	);
	for (const job of held) {
		const stopped = await page.request.post(`/api/jobs/${job.id}/cancel`, {
			headers: { 'x-csrf-token': await csrfOf(page) }
		});
		expect([204, 404], `could not stop the benchmark: ${await stopped.text()}`).toContain(
			stopped.status()
		);
	}
}

/** Stop watching a seeded folder and delete it from the media area. */
export async function removePhotos(page: Page, seeded: SeededFolder): Promise<void> {
	const gone = await page.request.delete(`/api/library/roots/${seeded.rootId}`, {
		headers: { 'x-csrf-token': await csrfOf(page) }
	});
	expect([200, 202, 204, 404], `could not remove the seeded folder`).toContain(gone.status());
	/* The server lets go of a folder it watched a moment after it answers, and until it has,
	   Windows refuses the removal. Asked again for a few seconds rather than once. */
	rmSync(seeded.directory, { recursive: true, force: true, maxRetries: 40, retryDelay: 250 });
}

/** Put people on files, as the file menu's Person picker does. */
export async function namePeopleOn(
	page: Page,
	assetIds: string[],
	personIds: string[]
): Promise<void> {
	const done = await page.request.post('/api/assets/people', {
		data: { asset_ids: assetIds, person_ids: personIds, add: true },
		headers: { 'x-csrf-token': await csrfOf(page) }
	});
	expect(done.ok(), `could not name people on files: ${await done.text()}`).toBeTruthy();
}

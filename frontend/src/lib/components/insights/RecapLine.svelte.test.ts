/* The announcement of a recap: the quiet line on Browse's header and the card at the top of
 * Insights, read from one list.
 *
 * What is held: the line says "Your September is ready" and opens the recap; the cross takes it
 * off the screen IMMEDIATELY and it stays off: a line that outlives its cross reads as a cross that
 * does not work. And the card on Insights goes with it, because the two are one announcement.
 * The recap itself is not dismissed from anything but the announcement: the server keeps it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const server = vi.hoisted(() => ({
	get: vi.fn(),
	post: vi.fn()
}));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: server.get, post: server.post }
}));

import RecapAnnouncement from './RecapAnnouncement.svelte';
import RecapLine from './RecapLine.svelte';

const SEPTEMBER = {
	id: 'r-sep',
	period: 'month:2026-09',
	title: 'Your September',
	span: 'September 2026',
	made_at: 1,
	seen_at: null,
	cards: 8
};

let hosts: HTMLElement[] = [];
let mounted: Record<string, unknown>[] = [];

beforeEach(() => {
	server.get.mockReset();
	server.post.mockReset();
	server.get.mockResolvedValue({ recaps: [SEPTEMBER], announced: SEPTEMBER });
	server.post.mockResolvedValue(undefined);
});

afterEach(() => {
	for (const one of mounted) void unmount(one);
	for (const one of hosts) one.remove();
	mounted = [];
	hosts = [];
});

async function settle(): Promise<void> {
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
}

async function draw(component: typeof RecapLine | typeof RecapAnnouncement): Promise<HTMLElement> {
	const host = document.createElement('div');
	document.body.append(host);
	hosts.push(host);
	mounted.push(mount(component, { target: host }));
	await settle();
	return host;
}

function cross(host: HTMLElement): HTMLButtonElement {
	const found = host.querySelector<HTMLButtonElement>('button[aria-label="Close"]');
	if (!found) throw new Error('no cross');
	return found;
}

describe("Browse's line", () => {
	it('says the recap is ready and opens it', async () => {
		const line = await draw(RecapLine);
		const open = line.querySelector('a');
		expect(open?.textContent).toBe('Your September is ready');
		expect(open?.getAttribute('href')).toBe('/insights/recaps/r-sep');
	});

	it('is gone the moment its cross is pressed, stays gone, and tells the server', async () => {
		const line = await draw(RecapLine);
		cross(line).click();
		flushSync();
		expect(line.textContent).not.toContain('is ready');
		await settle();
		expect(line.textContent).not.toContain('is ready');
		expect(server.post).toHaveBeenCalledWith('/insights/recaps/r-sep/dismiss');
	});

	it('draws nothing when nothing is announced', async () => {
		server.get.mockResolvedValue({ recaps: [SEPTEMBER], announced: null });
		const line = await draw(RecapLine);
		expect(line.textContent?.trim()).toBe('');
	});
});

describe('the card at the top of Insights', () => {
	it('reads "Your September \u00b7 Ready to read" and opens the recap', async () => {
		const card = await draw(RecapAnnouncement);
		expect(card.textContent?.replace(/\s+/g, ' ')).toContain('Your September \u00b7 Ready to read');
		expect(card.querySelector('a')?.getAttribute('href')).toBe('/insights/recaps/r-sep');
	});

	it("goes with Browse's line: one announcement, one cross", async () => {
		const card = await draw(RecapAnnouncement);
		const line = await draw(RecapLine);
		cross(card).click();
		flushSync();
		expect(card.textContent).not.toContain('Ready to read');
		expect(line.textContent).not.toContain('is ready');
	});
});

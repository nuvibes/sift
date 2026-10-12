import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * The swap's steps as the Manifest against the real server: the host's Start with its picks
 * weighed into groups, the guest's offer with a tick per group and the after number, and the
 * transfer with the file crossing now. The sessions and the weigh are SERVED: a swap needs a
 * second Sift, and this is about what the screens draw and send.
 */

function group(over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		key: 'person:0',
		kind: 'person',
		index: 0,
		name: 'Ava Example',
		match_name: null,
		matched_by: null,
		local_id: null,
		usernames: 0,
		faces: 0,
		files: 612,
		bytes: 17_900_000_000,
		have: null,
		to_take: 612,
		held_same: 0,
		held_look: 0,
		state: 'offered',
		done: 0,
		failed: 0,
		crossing: 0,
		left_out: null,
		sample: [],
		more: 0,
		...over
	};
}

function session(over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		id: 'm1',
		short_id: '825HYGXC',
		role: 'guest',
		state: 'offered',
		code: 'K7Q2ZP',
		token: null,
		sentence: null,
		peer_device: 'ABCDEFGHJKLMNPQR',
		offer: {
			layout: 'rows',
			rows: [],
			everyone: [],
			offered_files: 1412,
			offered_bytes: 38_200_000_000,
			files: 717,
			bytes: 19_500_000_000,
			shared: 0,
			unfiled_files: 0,
			unfiled_bytes: 0,
			held: []
		},
		offered_files: 1412,
		unwanted_files: 0,
		wanted_files: 717,
		wanted_bytes: 19_500_000_000,
		sent_files: 0,
		sent_bytes: 0,
		received_files: null,
		rate_bps: null,
		end_reason: null,
		ended_at: null,
		rejoin_until: null,
		started_at: 1,
		two_way: false,
		answered: false,
		sending: null,
		receiving: null,
		manifest: [
			group({ have: 203, to_take: 409, held_same: 32, held_look: 171 }),
			group({
				key: 'person:1',
				index: 1,
				name: 'Bryn Calloway',
				files: 308,
				have: 0,
				to_take: 308
			}),
			group({
				key: 'site:0',
				kind: 'site',
				index: null,
				name: 'Northlight Media',
				usernames: 6,
				have: 92,
				to_take: 349
			})
		],
		tunnel_name: null,
		...over
	};
}

async function serve(page: Page, path: string, body: unknown): Promise<void> {
	await page.route(path, (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
	);
}

test.describe('the swap as the Manifest', () => {
	test('the host Start draws the picks as groups, the kept-out row and what they learn', async ({
		page
	}) => {
		await signInAsAdmin(page);
		await serve(page, '**/api/swap/tunnels', [
			{ id: 't1', name: 'Harbor exit', can_host: true, endpoint: null, rate_bps: 200_000_000 }
		]);
		await serve(page, '**/api/swap/weigh', {
			files: 920,
			bytes: 27_000_000_000,
			left_out: [],
			left_out_other: 0,
			groups: [
				group(),
				group({ key: 'person:1', index: 1, name: 'Orla Tennant', left_out: 'local', files: 1200 }),
				group({ key: 'site:0', kind: 'site', index: null, name: 'Northlight Media', files: 308 })
			],
			learns: { people: 1, aliases: 0, sites: 1, usernames: 6, faces: 14 }
		});
		// A person of this run's own to pick: the seed holds none.
		const me = await (await page.request.get('/api/auth/me')).json();
		const name = `e2e-swap-${Date.now()}`;
		const made = await page.request.post('/api/people', {
			data: { name, vault: false },
			headers: { 'x-csrf-token': me.csrf_token as string }
		});
		expect(made.ok(), await made.text()).toBeTruthy();
		await page.goto('/swap?door=start');
		await page.getByRole('button', { name: 'Choose people' }).click();
		const sheet = page.getByRole('dialog');
		await sheet.getByRole('searchbox').fill(name);
		// The picker's choices are pressable rows in its list.
		await sheet.getByRole('list').getByRole('button', { name }).click();
		await sheet.getByRole('button', { name: /^Add 1$/ }).click();
		const list = page.getByRole('list', { name: 'What you are offering' });
		await expect(list).toContainText('Ava Example');
		await expect(list).toContainText('Kept local \u00b7 1,200 files not offered');
		await expect(page.getByText('1 person, 1 Site')).toBeVisible();
		await expect(
			page.getByText(/They learn these names, aliases, Sites and Usernames/)
		).toBeVisible();
	});

	test('the guest offer ticks per group, shows the after number, and sends the groups skipped', async ({
		page
	}) => {
		await signInAsAdmin(page);
		await serve(page, '**/api/swap/tunnels', []);
		await serve(page, '**/api/swap/sessions/m1', session());
		await serve(page, '**/api/swap/sessions/m1/code', session());
		await serve(page, '**/api/swap/sessions/m1/weigh', {
			files: 409,
			bytes: 11_000_000_000,
			left_out: [],
			left_out_other: 0
		});
		let sent: unknown = null;
		await page.route('**/api/swap/sessions/m1/take', async (route) => {
			sent = route.request().postDataJSON();
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(session({ state: 'transferring', answered: true }))
			});
		});
		await page.goto('/swap?session=m1');
		await page.getByRole('button', { name: 'They match' }).click();
		const list = page.getByRole('list', { name: 'What they offered' });
		await expect(list).toContainText('203 \u2192 612');
		await expect(page.getByText(/32 the very same files and 171 that look the same/)).toBeVisible();
		await page.getByRole('checkbox', { name: /^Take Bryn/ }).click();
		await page.getByRole('checkbox', { name: 'Take Northlight Media' }).click();
		await page.getByRole('button', { name: 'Take 409 files' }).click();
		await expect
			.poll(() => sent)
			.toEqual({ skipped: [1], unticked: [], skipped_groups: ['site:0'] });
	});

	test('the transfer draws the same rows with the file crossing now', async ({ page }) => {
		await signInAsAdmin(page);
		await serve(
			page,
			'**/api/swap/sessions/m1',
			session({
				state: 'transferring',
				answered: true,
				sent_files: 409,
				sent_bytes: 11_000_000_000,
				received_files: 410,
				rate_bps: 192_800_000,
				streams: 16,
				now: { title: 'beach set 03.mp4', size: 1_200_000_000, group: 'person:1' },
				manifest: [
					group({ state: 'landed', done: 409 }),
					group({
						key: 'person:1',
						index: 1,
						name: 'Bryn Calloway',
						state: 'crossing',
						done: 3,
						to_take: 308
					})
				]
			})
		);
		await page.goto('/swap?session=m1');
		await expect(page.getByText('24 MB/s on 16 streams', { exact: false })).toBeVisible();
		const list = page.getByRole('list', { name: 'What the swap carries' });
		await expect(list).toContainText('Now: beach set 03.mp4, 1.2 GB');
		await expect(list).toContainText('Landed');
		await expect(list).toContainText('3 of 308');
	});
});

import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { startFakeStashBoxes, type FakeStashBoxes } from './fake-stash-box';
import { csrfOf, removePeople } from './seed';

/*
 * Settling what two stash-boxes say about one person, on that person's page.
 *
 * The journey: a person is linked to two boxes, one of which disagrees about a field. The
 * disagreement is drawn at the top of the person's History. Taking the box's value writes it, the
 * row leaves and does not come back under the OTHER box's name (which now disagrees with the value
 * just taken, and is set aside by the same press), the receipt names the field, both values and the
 * box set aside, and Undo puts all of it back.
 *
 * Every row here comes from the server asking a box: the link fetches the entry by id, and the
 * disagreement is worked out from what the two boxes said against the record. The boxes are
 * `fake-stash-box.ts`, reached through each box's route.
 */

const PERSON = 'Bryn Calloway';

let fake: FakeStashBoxes;

test.beforeAll(async () => {
	fake = await startFakeStashBoxes({
		northbox: [{ id: 'nb-1', name: PERSON, hair_color: 'Blonde' }],
		southbox: [{ id: 'sb-1', name: PERSON, hair_color: 'Auburn' }]
	});
});

test.afterAll(async () => {
	await fake.close();
});

test('taking one box value sets the other aside, says so, and Undo puts it back', async ({
	page
}) => {
	await signInAsAdmin(page);
	const token = await csrfOf(page);
	const headers = { 'x-csrf-token': token };

	const boxes: string[] = [];
	const people: string[] = [];
	try {
		for (const [name, host] of [
			['Northbox', 'northbox'],
			['Southbox', 'southbox']
		]) {
			const added = await page.request.post('/api/stash-boxes', {
				headers,
				data: {
					name,
					endpoint: fake.endpointOf(host),
					/* Invented. The stand-in reads no key; a box is only asked with one it can open. */
					api_key: 'e2e-invented-key',
					route: fake.route
				}
			});
			expect(added.ok(), `could not add ${name}: ${await added.text()}`).toBeTruthy();
			boxes.push(String((await added.json()).id));
		}

		const made = await page.request.post('/api/people', {
			headers,
			data: { name: PERSON, vault: false }
		});
		expect(made.ok(), `could not make the person: ${await made.text()}`).toBeTruthy();
		const personId = String((await made.json()).id);
		people.push(personId);
		const edited = await page.request.put(`/api/people/${personId}`, {
			headers,
			data: { name: PERSON, record: { hair_color: 'Auburn' } }
		});
		expect(
			edited.ok(),
			`could not give the person a hair colour: ${await edited.text()}`
		).toBeTruthy();

		for (const [boxId, remote] of [
			[boxes[0], 'nb-1'],
			[boxes[1], 'sb-1']
		]) {
			const linked = await page.request.put(`/api/stash-boxes/links/person/${personId}/${boxId}`, {
				headers,
				data: { remote_id: remote }
			});
			expect(linked.ok(), `could not link: ${await linked.text()}`).toBeTruthy();
		}
		expect(fake.asked.map((one) => one.host)).toEqual(['northbox', 'southbox']);

		await page.goto(`/people/${personId}?show=history`);
		const panel = page.getByRole('region', { name: 'Where a stash-box disagrees' });
		const rows = panel.locator('tbody tr');
		/* One row: Southbox agrees with the record, Northbox does not. */
		await expect(rows).toHaveCount(1);
		await expect(rows.first()).toContainText('Northbox');
		await expect(rows.first()).toContainText('Auburn');
		await expect(rows.first()).toContainText('Blonde');

		await rows.first().getByRole('button', { name: 'Take theirs' }).click();

		/* The row leaves and stays gone: Southbox's now-different answer is set aside, not asked. */
		await expect(rows).toHaveCount(0);
		const receipt = page.getByText(/^Took Northbox's hair colou?r for Bryn Calloway/i).first();
		await expect(receipt).toBeVisible();
		await expect(receipt).toContainText('Blonde');
		await expect(receipt).toContainText('Auburn');
		await expect(receipt).toContainText(/set aside Southbox's Auburn/);

		const record = await page.request.get(`/api/stash-boxes/record/person/${personId}`);
		expect((await record.json()).values.hair_color).toBe('Blonde');

		await page.getByRole('button', { name: 'Undo', exact: true }).click();
		/* The state is the proof rather than the toast, which is gone in a few seconds: the row is
		   a question again and the thread says the decision was undone. */
		await expect(rows).toHaveCount(1);
		await expect(page.getByText('That decision was undone')).toBeVisible();
		await expect(rows.first()).toContainText('Northbox');
		const after = await page.request.get(`/api/stash-boxes/record/person/${personId}`);
		expect((await after.json()).values.hair_color).toBe('Auburn');
	} finally {
		await removePeople(page, people);
		for (const id of boxes) {
			await page.request.delete(`/api/stash-boxes/${id}`, { headers });
		}
	}
});

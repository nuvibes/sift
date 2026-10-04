import { expect, test, type Page } from '@playwright/test';
import { ADMIN, PASSWORD, signInAsAdmin } from './admin';

/* The Jobs dashboard, in a browser, against the real server.
 *
 * What no other kind of test can see is who is turned away. The dashboard reports what Sift is
 * doing with the files in somebody's library, which is a picture of that library. Not rendering
 * the nav item is not what keeps a guest out of it, and this asks the server directly, with no
 * interface involved.
 *
 * The connection that tells this screen when the queue has moved is one connection for the whole
 * application, so it is proved where it lives. See the live-connection spec.
 */

test('the dashboard is a screen, not a placeholder', async ({ page }) => {
	await signInAsAdmin(page);
	await page.goto('/settings/tasks?show=now');

	await expect(page.getByRole('heading', { name: 'Tasks and Activity' })).toBeVisible();
	await expect(page.getByText('Nothing here yet.')).toHaveCount(0);
});

test('the queue is drawn from what the server answered with, not left blank', async ({ page }) => {
	/* What is worth asserting is that the screen draws what the server answered with rather than
	 * rendering nothing, on a row that is really there, after waiting for it. The empty message
	 * is not a signal: the screen draws it while `queue.page` is still null.
	 *
	 * The row is arranged, not assumed: a fresh install queues nothing on its own, so the test runs
	 * a task the way `Settings > Tasks and Activity > Tasks` does and looks for the row its answer says is on Activity,
	 * under the name the server gives that row.
	 */
	await signInAsAdmin(page);
	const me = await page.request.get('/api/auth/me');
	const started = await page.request.post('/api/tasks/duplicates/run', {
		data: {},
		headers: { 'x-csrf-token': (await me.json()).csrf_token as string }
	});
	expect(started.ok(), await started.text()).toBeTruthy();
	const answer = (await started.json()) as { job_ids: string[]; on_activity: boolean };
	expect(answer.on_activity, 'the task chosen here is one Activity leaves off').toBe(true);

	const queue = (await (await page.request.get('/api/jobs?limit=50')).json()) as {
		jobs: { id: string; name: string }[];
	};
	const row = queue.jobs.find((job) => answer.job_ids.includes(job.id));
	expect(row, 'the task queued no job').toBeTruthy();

	await page.goto('/settings/tasks?show=now');

	await expect(page.getByText(row!.name).first()).toBeVisible();
	await expect(page.getByText('No tasks are running or waiting.')).toHaveCount(0);
});

test.describe('who is turned away', () => {
	test('somebody signed out gets nothing from the API itself', async ({ request }) => {
		// No page, no navigation, no nav item to not render. This is the request a stolen guess would
		// make, and the server answering it is the only thing that has ever kept anyone out.
		const listed = await request.get('/api/jobs');

		expect(listed.status()).toBe(401);
	});
});

import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { ADMIN, PASSWORD, signInAsAdmin } from './admin';

/* The dashboard of jobs, against the real server: the nav item not drawing is not what keeps a
 * guest out, so the server is asked directly. */

test('the dashboard is a screen, not a placeholder', async ({ page }) => {
	await signInAsAdmin(page);
	await page.goto('/settings/tasks?show=now');

	await expect(page.getByRole('heading', { name: 'Tasks and Activity' })).toBeVisible();
	await expect(page.getByText('Nothing here yet.')).toHaveCount(0);
});

test('the queue is drawn from what the server answered with, not left blank', async ({ page }) => {
	/* A real row from the server's answer, after running a task as Settings does: a fresh install
	 * queues nothing, and the empty message is drawn while loading. */
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
		// The request a stolen guess would make; the server is what keeps anyone out.
		const listed = await request.get('/api/jobs');

		expect(listed.status()).toBe(401);
	});
});

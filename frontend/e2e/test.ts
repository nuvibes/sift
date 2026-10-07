import { test as base, expect, type Page } from '@playwright/test';

export { expect };

/** The page's own word on a failure: console errors, uncaught errors, requests that failed or
    were refused. Attached beside the photograph only when the test did not end as expected, so
    a blank page on a runner says why. */
export const test = base.extend<{ page: Page }>({
	page: async ({ page }, use, testInfo) => {
		const said: string[] = [];
		page.on('console', (message) => {
			if (message.type() === 'error' || message.type() === 'warning') {
				said.push(`console.${message.type()}: ${message.text()}`);
			}
		});
		page.on('pageerror', (error) => said.push(`pageerror: ${error.message}`));
		page.on('requestfailed', (request) =>
			said.push(
				`requestfailed: ${request.method()} ${request.url()} ${request.failure()?.errorText}`
			)
		);
		page.on('response', (response) => {
			if (response.status() >= 400) {
				said.push(`http ${response.status()}: ${response.request().method()} ${response.url()}`);
			}
		});
		await use(page);
		if (testInfo.status !== testInfo.expectedStatus && said.length > 0) {
			await testInfo.attach('page-said.txt', {
				body: said.join('\n'),
				contentType: 'text/plain'
			});
		}
	}
});

/* Serving one screen off the disk, for the moment when there is no server to serve it. */

import { net, protocol } from 'electron';
import * as path from 'node:path';
import { pathToFileURL } from 'node:url';

import { clientFiles } from './paths';

/** The scheme, and the one host under it. A URL of any other shape is refused. */
export const SHELL_SCHEME = 'sift-shell';
export const SHELL_ORIGIN = `${SHELL_SCHEME}://app`;

/** Declare the scheme before the app is ready. It cannot be done later. */
export function declareShellScheme(): void {
	protocol.registerSchemesAsPrivileged([
		{
			scheme: SHELL_SCHEME,
			privileges: { standard: true, secure: true, supportFetchAPI: true }
		}
	]);
}

/** Wire the scheme up to the built client. Called once, after the app is ready. */
export function serveShellPages(): void {
	const root = clientFiles();

	protocol.handle(SHELL_SCHEME, async (request) => {
		const url = new URL(request.url);
		/* One host, and nothing else. Without this, `sift-shell://anything/../../secret` is a URL
		 * somebody could put in front of this handler. */
		if (url.host !== 'app') return new Response('Not found', { status: 404 });

		const target = resolveWithin(root, url.pathname);
		if (target === null) return new Response('Not found', { status: 404 });
		return net.fetch(pathToFileURL(target).toString());
	});
}

/** Where a request lands on the disk, or null if it lands outside the client's own folder. */
export function resolveWithin(root: string, pathname: string): string | null {
	const decoded = safeDecode(pathname);
	if (decoded === null) return null;
	const resolvedRoot = path.resolve(root);
	const target = path.resolve(resolvedRoot, `.${decoded}`);
	const inside =
		target === resolvedRoot || target.startsWith(resolvedRoot + path.sep);
	if (!inside) return null;
	return path.extname(target) === '' ? path.join(resolvedRoot, 'index.html') : target;
}

function safeDecode(pathname: string): string | null {
	try {
		const decoded = decodeURIComponent(pathname);
		/* A NUL truncates a path in every C API underneath, so a name carrying one means one thing
		 * to this check and another to the filesystem. */
		return decoded.includes('\0') ? null : decoded;
	} catch {
		return null;
	}
}

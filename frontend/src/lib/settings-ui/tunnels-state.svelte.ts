/* Named ways out to the internet, and which sites take which. */

import { api, ApiError } from '$lib/api/client';
import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
import { UNREACHABLE } from '$lib/shell/unreachable';
import type { components } from '$lib/api/schema';

/** The route meaning the machine's own address. Matches the server's word for it. */
export const DIRECT = 'direct';

/** The scope the default route is stored under. Not a site, and no site key can equal it. */
export const DEFAULT_SCOPE = '*';

export type Tunnel = components['schemas']['TunnelItem'];

export type SiteChoice = components['schemas']['SiteChoice'];

type Routes = components['schemas']['RoutesResponse'];

/** The address to show beside a tunnel right now, or null when there is none worth showing. */
export function addressOf(tunnel: Tunnel): string | null {
	return tunnel.up && tunnel.endpoint ? tunnel.endpoint : null;
}

/** The phrase `ExitAddress` builds its control's name from, for a tunnel: one wording everywhere. */
export function addressPhrase(name: string, when: 'now' | 'then' = 'now'): string {
	return when === 'now'
		? `the address of the server ${name} is connected to`
		: `the address of the server ${name} was connected to`;
}

function reason(error: unknown): string {
	if (error instanceof ApiError) return error.detail ?? error.message;
	return UNREACHABLE;
}

export class Tunnels {
	items = $state<Tunnel[]>([]);
	defaultRoute = $state<string>(DIRECT);
	siteRoutes = $state<Record<string, string>>({});
	sites = $state<SiteChoice[]>([]);
	loaded = $state(false);
	problem = $state<string | null>(null);
	importError = $state<string | undefined>(undefined);
	busy = $state(false);

	async load(): Promise<void> {
		try {
			const [tunnels, routes] = await Promise.all([
				api.get<Tunnel[]>('/tunnels'),
				api.get<Routes>('/download-routes')
			]);
			this.items = tunnels;
			this.defaultRoute = routes.default;
			this.siteRoutes = routes.sites;
			this.sites = routes.available;
			this.problem = null;
		} catch (error) {
			this.problem = reason(error);
		}
		this.loaded = true;
	}

	/* The tunnel a site's traffic actually takes. Two screens ask this (the chooser in settings,
	 * and the summary above the queue), and the rule is not "what is in the box beside its
	 * name". */
	tunnelFor(siteKey: string): Tunnel | null {
		const chosen = this.siteRoutes[siteKey] ?? this.defaultRoute;
		if (chosen === DIRECT) return null;
		return this.items.find((one) => one.id === chosen) ?? null;
	}

	/** Whether a site is pointed at a tunnel that does not exist any more. */
	isOrphaned(siteKey: string): boolean {
		const chosen = this.siteRoutes[siteKey];
		if (!chosen || chosen === DIRECT) return false;
		return !this.items.some((one) => one.id === chosen);
	}

	/** The same question about the default, which can be orphaned in exactly the same way. */
	get defaultIsOrphaned(): boolean {
		return this.defaultRoute !== DIRECT && !this.items.some((one) => one.id === this.defaultRoute);
	}

	/** Every site going out through a tunnel, connected or not, in the listed order. */
	get tunneled(): { site: SiteChoice; tunnel: Tunnel }[] {
		return this.sites.flatMap((site) => {
			const tunnel = this.tunnelFor(site.key);
			return tunnel ? [{ site, tunnel }] : [];
		});
	}

	/* Watching a tunnel that is on its way up or down. */
	#watching: ReturnType<typeof setInterval> | null = null;

	/** True while any tunnel is switched on and not yet carrying anything. */
	get settling(): boolean {
		return this.items.some((tunnel) => tunnel.enabled && !tunnel.up);
	}

	/** Read the list again whenever a setting moves anywhere: a tunnel added, renamed, started,
	 * stopped or removed, or a Site's route chosen, in another window or by another admin. */
	follow(): void {
		whenChanged(settingChanges, () => void this.load());
	}

	/** Keep the list fresh while anything is still coming up. Safe to call more than once. */
	watch(everyMs = 3000): () => void {
		this.#watching ??= setInterval(() => {
			if (this.settling) void this.load();
		}, everyMs);
		return () => this.unwatch();
	}

	unwatch(): void {
		if (this.#watching !== null) clearInterval(this.#watching);
		this.#watching = null;
	}

	/** Import a provider's configuration. Returns a refusal to show under the box, or none. */
	async add(name: string, config: string): Promise<string | undefined> {
		this.busy = true;
		this.importError = undefined;
		try {
			await api.post('/tunnels', { body: { name, config } });
			await this.load();
			return undefined;
		} catch (error) {
			return reason(error);
		} finally {
			this.busy = false;
		}
	}

	/** Turn a tunnel on. The server waits for the far end to answer before it answers, so this
	 * takes a moment and a green control afterwards means a tunnel that is really carrying. */
	async start(tunnel: Tunnel): Promise<string | undefined> {
		return this.act(() => api.post(`/tunnels/${tunnel.id}/start`, { body: {} }));
	}

	/** Turn a tunnel off. The downloads already on it finish first unless `now`. */
	async stop(tunnel: Tunnel, now = false): Promise<string | undefined> {
		return this.act(() => api.post(`/tunnels/${tunnel.id}/stop`, { body: { now } }));
	}

	async remove(tunnel: Tunnel): Promise<string | undefined> {
		return this.act(() => api.del(`/tunnels/${tunnel.id}`));
	}

	/** Swap in a configuration the provider reissued. */
	async replaceConfig(tunnel: Tunnel, config: string): Promise<string | undefined> {
		return this.act(() => api.post(`/tunnels/${tunnel.id}/config`, { body: { config } }));
	}

	async rename(tunnel: Tunnel, name: string): Promise<string | undefined> {
		return this.act(() => api.patch(`/tunnels/${tunnel.id}`, { body: { name } }));
	}

	/** Point one site, or everything under the default scope, at a way out. */
	async route(scope: string, route: string): Promise<string | undefined> {
		return this.act(() => api.put(`/download-routes/${scope}`, { body: { route } }));
	}

	/** Put a site back to following the default. */
	async clearRoute(scope: string): Promise<string | undefined> {
		return this.act(() => api.del(`/download-routes/${scope}`));
	}

	private async act(call: () => Promise<unknown>): Promise<string | undefined> {
		this.busy = true;
		try {
			await call();
			await this.load();
			return undefined;
		} catch (error) {
			return reason(error);
		} finally {
			this.busy = false;
		}
	}
}

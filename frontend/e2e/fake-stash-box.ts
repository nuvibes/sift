import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';

/*
 * A stand-in stash-box, for a journey that needs the server to have asked one.
 *
 * What a box says is decided on the server: a link fetches the entry fresh by id, and a disagreement
 * is worked out from what two boxes' stored answers say against the record. So a spec cannot seed
 * one with a request of its own: the server has to ask something, and this is what it asks.
 *
 * It answers the one question a link puts (`findPerformer` by id) for any number of boxes, each
 * named by the host in its endpoint. Anything else is answered with an `errors` array, the shape a
 * real box uses for a question it does not understand, so a new question the server starts asking
 * fails loudly here rather than being answered with nothing.
 *
 * ## How the server reaches it
 *
 * Through the box's ROUTE, as an HTTP proxy. The connector every outbound question goes through
 * refuses a private or local address, and a box's endpoint is checked against that rule like any
 * other address, which is right, and this does not step around it. What it allows is a route: a
 * box is sent through a proxy listening on this machine, the way a tunnel's is, and the endpoint's
 * host is a name that never resolves (`.invalid`) because the proxy is what answers for it. So the
 * endpoints here are `http://<box>.invalid/graphql` and the route is this server's own address.
 */

/** One entry a fake box holds, in the shape `findPerformer` answers with. */
export interface FakePerformer {
	id: string;
	name: string;
	hair_color?: string;
	country?: string;
}

export interface FakeStashBoxes {
	/** Where a box's traffic is to be sent: this server, as the box's route. */
	route: string;
	/** The endpoint to configure for the box of this host name. */
	endpointOf(host: string): string;
	/** Every question asked, by host, in order, so a spec can say the server really asked. */
	asked: { host: string; query: string; variables: Record<string, unknown> }[];
	close(): Promise<void>;
}

/** Every field `findPerformer` is asked for, answered as a box with nothing to say about it would. */
function performer(one: FakePerformer): Record<string, unknown> {
	return {
		id: one.id,
		name: one.name,
		disambiguation: null,
		aliases: [],
		gender: null,
		birth_date: null,
		country: one.country ?? null,
		ethnicity: null,
		eye_color: null,
		hair_color: one.hair_color ?? null,
		height: null,
		cup_size: null,
		band_size: null,
		waist_size: null,
		hip_size: null,
		breast_type: null,
		career_start_year: null,
		career_end_year: null,
		tattoos: [],
		piercings: [],
		images: [],
		urls: [],
		scene_count: 0,
		studios: [],
		created: null,
		updated: null,
		merged_into_id: null
	};
}

function read(request: IncomingMessage): Promise<string> {
	return new Promise((resolve, reject) => {
		const parts: Buffer[] = [];
		request.on('data', (part: Buffer) => parts.push(part));
		request.on('end', () => resolve(Buffer.concat(parts).toString('utf8')));
		request.on('error', reject);
	});
}

function answer(response: ServerResponse, body: unknown): void {
	response.writeHead(200, { 'Content-Type': 'application/json' });
	response.end(JSON.stringify(body));
}

/**
 * Start the stand-in, holding `boxes`: each host name's entries, by id.
 *
 * On a port the system chooses, so specs running at the same time each have their own.
 */
export async function startFakeStashBoxes(
	boxes: Record<string, FakePerformer[]>
): Promise<FakeStashBoxes> {
	const asked: FakeStashBoxes['asked'] = [];
	const server: Server = createServer((request, response) => {
		void (async () => {
			/* A proxied request names the whole address; the host is which box it is for. */
			const host = new URL(request.url ?? '/', `http://${request.headers.host ?? 'unknown'}`)
				.hostname;
			const box = host.endsWith('.invalid') ? host.slice(0, -'.invalid'.length) : host;
			let sent: { query?: string; variables?: Record<string, unknown> } = {};
			try {
				sent = JSON.parse(await read(request));
			} catch {
				response.writeHead(400);
				response.end();
				return;
			}
			const query = String(sent.query ?? '');
			const variables = sent.variables ?? {};
			asked.push({ host: box, query, variables });
			const held = boxes[box];
			if (held === undefined) {
				response.writeHead(404);
				response.end();
				return;
			}
			if (query.includes('findPerformer(')) {
				const found = held.find((one) => one.id === variables.id);
				answer(response, { data: { findPerformer: found ? performer(found) : null } });
				return;
			}
			answer(response, { errors: [{ message: 'this stand-in only answers findPerformer' }] });
		})();
	});
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	const { port } = server.address() as AddressInfo;
	return {
		route: `http://127.0.0.1:${port}`,
		endpointOf: (host: string) => `http://${host}.invalid/graphql`,
		asked,
		close: () => new Promise<void>((resolve) => server.close(() => resolve()))
	};
}

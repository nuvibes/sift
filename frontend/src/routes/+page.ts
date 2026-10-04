import { redirect } from '@sveltejs/kit';

// The library is what Sift is for, so that is where the front door goes. `/` keeps no state of its
// own. Landing somewhere real means the address bar always says where you are, from the first
// moment, and there is no page that exists only to bounce you off it.
export function load({ url }: { url: URL }): never {
	/* The query rides along: a link into Browse filtered by a filter (`/?enriched=stash` from
	   the recognized pile's empty state) has to land narrowed, not on the whole library. */
	redirect(307, `/browse${url.search}`);
}

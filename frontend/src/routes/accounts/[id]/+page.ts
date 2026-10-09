import { redirect } from '@sveltejs/kit';

/* An OLD ADDRESS, kept so a pasted link lands somewhere. */
export function load({ params }: { params: { id: string } }): never {
	redirect(308, `/browse?username=${encodeURIComponent(params.id)}`);
}

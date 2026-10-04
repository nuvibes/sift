<script>
  // Rule tests for frontend.yml. Not a component.
  export let asset;
  export let description;
</script>

<!-- ruleid: sift-no-svelte-html-directive -->
<h1>{@html asset.title}</h1>

<!-- ruleid: sift-no-svelte-html-directive -->
<div class="description">{@html description}</div>

<!-- ok: sift-no-svelte-html-directive -->
<h1>{asset.title}</h1>

<!-- ok: sift-no-svelte-html-directive -->
<div class="description">{description}</div>

<!-- ok: sift-no-svelte-html-directive -->
<img src={asset.thumbnail_url} alt={asset.title} />

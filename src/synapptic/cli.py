"""Click CLI for synaptic."""

import json
import shutil
import sys
from pathlib import Path

import click
import yaml

from synapptic.config import SYNAPPTIC_DIR
from synapptic.state import (
    ensure_dirs,
    find_all_observations,
    find_session_transcripts,
    get_processed_session_ids,
    get_profile_history,
    get_queue,
    clear_queue,
    list_projects,
    load_archetype,
    load_observations,
    load_profile,
    reset_all,
    save_archetype,
    save_observations,
    save_profile,
)


@click.group()
@click.version_option()
def cli():
    """The missing feedback loop for agentic development."""
    ensure_dirs()


@cli.group("config")
def config_group():
    """View and modify synaptic configuration."""
    pass


@config_group.command("show")
def config_show():
    """Show current configuration."""
    from synapptic.providers import PROVIDERS, load_config

    config = load_config()
    provider = config.get("provider", "claude-cli")
    provider_name = PROVIDERS.get(provider, {}).get("name", provider)
    model = config.get("model", "sonnet")
    mode = config.get("profiling_mode", "both")
    outputs = config.get("outputs", ["claude-code"])
    api_key = config.get("api_key", "")
    api_url = config.get("api_url", "")

    click.echo(f"Provider:        {provider_name} ({provider})")
    click.echo(f"Model:           {model}")
    click.echo(f"Profiling mode:  {mode}")
    click.echo(f"Outputs:         {', '.join(outputs)}")
    if api_key:
        click.echo(f"API key:         ...{api_key[-8:]}")
    if api_url:
        click.echo(f"API URL:         {api_url}")
    click.echo(f"Config file:     ~/.synapptic/config.yaml")


@config_group.command("provider")
def config_provider():
    """Set LLM provider and model."""
    from synapptic.providers import PROVIDERS, load_config, save_config

    config = load_config()

    click.echo("LLM provider for extraction and synthesis:\n")
    provider_keys = list(PROVIDERS.keys())
    for i, key in enumerate(provider_keys, 1):
        info = PROVIDERS[key]
        current = " (current)" if key == config.get("provider") else ""
        click.echo(f"  {i}. {info['name']}{current}")
        click.echo(f"     {info['description']}")

    click.echo()
    default_idx = provider_keys.index(config.get("provider", "claude-cli")) + 1
    choice = click.prompt("Select provider", type=click.IntRange(1, len(provider_keys)),
                          default=default_idx)
    provider = provider_keys[choice - 1]
    info = PROVIDERS[provider]
    config["provider"] = provider

    # Model - always default to the provider's recommended model
    default_model = info.get("default_model", "sonnet")
    config["model"] = click.prompt("Model", default=default_model)

    # API key
    if info.get("requires_key"):
        existing_key = config.get("api_key", "")
        masked = f"...{existing_key[-8:]}" if len(existing_key) > 8 else "(not set)"
        click.echo(f"  Current key: {masked}")
        new_key = click.prompt("API key", default="", show_default=False)
        if new_key:
            config["api_key"] = new_key

    # API URL
    if info.get("requires_url") or info.get("default_url"):
        default_url = info.get("default_url", config.get("api_url", ""))
        config["api_url"] = click.prompt("API URL", default=default_url)

    save_config(config)
    click.echo(f"\nProvider set: {info['name']}, Model: {config['model']}")


@config_group.command("mode")
def config_mode():
    """Set profiling mode: what to extract from sessions."""
    from synapptic.providers import load_config, save_config

    config = load_config()
    current = config.get("profiling_mode", "both")

    click.echo("Profiling mode — what to extract from sessions:\n")
    modes = [
        ("user", "User profiling only — preferences, workflow, communication style, expertise"),
        ("agent", "Agent profiling only — AI failures, guards, known weaknesses"),
        ("both", "Both user and agent profiling (recommended)"),
    ]
    for i, (key, desc) in enumerate(modes, 1):
        marker = " (current)" if key == current else ""
        click.echo(f"  {i}. {key}{marker}")
        click.echo(f"     {desc}")

    click.echo()
    mode_keys = [m[0] for m in modes]
    default_idx = mode_keys.index(current) + 1
    choice = click.prompt("Select mode", type=click.IntRange(1, 3), default=default_idx)
    config["profiling_mode"] = mode_keys[choice - 1]

    save_config(config)
    click.echo(f"\nProfiling mode set: {config['profiling_mode']}")


@config_group.command("outputs")
def config_outputs():
    """Set output targets — where to write the archetype."""
    from synapptic.outputs import OUTPUTS
    from synapptic.providers import load_config, save_config

    config = load_config()
    existing_outputs = config.get("outputs", ["claude-code"])

    click.echo("Output targets (where to write the archetype):\n")
    output_keys = list(OUTPUTS.keys())
    for i, key in enumerate(output_keys, 1):
        info = OUTPUTS[key]
        checked = "x" if key in existing_outputs else " "
        click.echo(f"  {i}. [{checked}] {info['name']} — {info['description']}")

    click.echo()
    default_sel = ",".join(str(output_keys.index(o) + 1) for o in existing_outputs if o in output_keys)
    selections = click.prompt("Select outputs (comma-separated numbers)", default=default_sel)
    selected = []
    for s in selections.split(","):
        s = s.strip()
        if s.isdigit() and 1 <= int(s) <= len(output_keys):
            selected.append(output_keys[int(s) - 1])
    config["outputs"] = selected or ["claude-code"]

    save_config(config)
    click.echo(f"\nOutputs set: {', '.join(config['outputs'])}")


@cli.command("init")
@click.pass_context
def init_cmd(ctx):
    """Interactive setup — runs all config steps in sequence."""
    click.echo("=== synapptic setup ===\n")
    ctx.invoke(config_provider)
    click.echo()
    ctx.invoke(config_mode)
    click.echo()
    ctx.invoke(config_outputs)
    click.echo(f"\nSetup complete. Run 'synapptic install' then 'synapptic update'.")


@cli.group("patterns")
def patterns_group():
    """Manage extraction patterns."""
    pass


@patterns_group.command("list")
def patterns_list():
    """Show available extraction patterns."""
    from synapptic.patterns import list_patterns
    from synapptic.providers import load_config

    config = load_config()
    active = config.get("pattern", "default")

    found = list_patterns()
    if not found:
        click.echo("No patterns found.")
        return

    for p in found:
        marker = " (active)" if p["name"] == active else ""
        click.echo(f"  {p['name']}{marker} [{p['source']}]")


@patterns_group.command("show")
@click.argument("name", default="default")
def patterns_show(name):
    """Show a pattern's prompt template."""
    from synapptic.patterns import load_pattern

    content = load_pattern(name)
    if not content:
        click.echo(f"Pattern '{name}' not found.")
        return
    click.echo(content)


@patterns_group.command("create")
@click.argument("name")
def patterns_create(name):
    """Create a new custom pattern from the default template."""
    from synapptic.patterns import create_pattern

    path = create_pattern(name)
    click.echo(f"Pattern created: {path}")
    click.echo(f"Edit the prompt.md file, then: synapptic config set pattern {name}")


@patterns_group.command("use")
@click.argument("name")
def patterns_use(name):
    """Set the active extraction pattern."""
    from synapptic.patterns import load_pattern
    from synapptic.providers import load_config, save_config

    if not load_pattern(name):
        click.echo(f"Pattern '{name}' not found. Run 'synapptic patterns list' to see available.")
        return

    config = load_config()
    config["pattern"] = name
    save_config(config)
    click.echo(f"Active pattern set to: {name}")


@cli.command()
@click.option("--session", "-s", help="Session UUID to extract")
@click.option("--all", "extract_all", is_flag=True, help="Extract all unprocessed sessions")
@click.option("--max-tokens", default=50_000, help="Max tokens per session after filtering")
@click.option("--model", default=None, help="Override model (default: from config)")
@click.option("--min-lines", default=20, help="Skip transcripts with fewer lines than this")
def extract(session, extract_all, max_tokens, model, min_lines):
    """Extract observations from session transcripts (Tier 1)."""
    from synapptic.filter import filter_transcript
    from synapptic.extract import extract_observations
    from synapptic.profile import route_observations
    from synapptic.providers import load_config

    llm_config = load_config()
    if model:
        llm_config["model"] = model

    transcripts = find_session_transcripts()

    if session:
        session_ids = [session]
    elif extract_all:
        processed = get_processed_session_ids()
        session_ids = [sid for sid in transcripts if sid not in processed]
        click.echo(f"Found {len(session_ids)} unprocessed sessions")
    else:
        queued = get_queue()
        if queued:
            session_ids = queued
            click.echo(f"Processing {len(session_ids)} queued sessions")
        else:
            click.echo("No sessions to process. Use --session UUID, --all, or queue sessions.")
            return

    # Load global profile for profile-aware extraction
    global_profile = load_profile(project_slug=None)
    has_profile = bool(global_profile.get("dimensions"))
    if has_profile:
        n_prefs = sum(len(v) for v in global_profile["dimensions"].values())
        click.echo(f"Profile-aware extraction ({n_prefs} global preferences)")

    total_observations = 0
    for sid in session_ids:
        if sid not in transcripts:
            click.echo(f"  Session {sid[:8]}... not found, skipping")
            continue

        path, project_slug = transcripts[sid]
        size_mb = path.stat().st_size / 1024 / 1024
        click.echo(f"  {sid[:8]}... [{project_slug}] ({size_mb:.1f}MB)", nl=False)

        line_count = count_lines(path)
        if line_count < min_lines:
            click.echo(f" — skipped ({line_count} lines)")
            save_observations(sid, [], project_slug=project_slug)
            continue

        turns = filter_transcript(path, max_tokens=max_tokens)
        if not turns:
            click.echo(" — no content")
            save_observations(sid, [], project_slug=project_slug)
            continue

        user_turns = sum(1 for t in turns if t.role == "user")
        boosted = sum(1 for t in turns if t.boosted)
        click.echo(f" → {len(turns)} turns ({user_turns} user, {boosted} boosted)", nl=False)

        # Also load project profile for combined context
        project_profile = load_profile(project_slug=project_slug)
        combined_profile = merge_profiles_for_context(global_profile, project_profile) if has_profile else None

        observations = extract_observations(
            turns, sid,
            profile=combined_profile,
            config=llm_config,
        )

        # Tag each observation with its project
        for obs in observations:
            obs["project"] = project_slug

        # Route: global dimensions → global, project dimensions → project
        global_obs, project_obs = route_observations(observations)

        # Save under the project (all observations stored per-project for traceability)
        save_observations(sid, observations, project_slug=project_slug)

        total_observations += len(observations)
        g = len(global_obs)
        p = len(project_obs)
        click.echo(f" → {len(observations)} obs ({g} global, {p} project)")

    if extract_all or get_queue():
        clear_queue()

    click.echo(f"\nTotal: {total_observations} observations extracted")


@cli.command()
@click.option("--project", "-p", help="Show profile for a specific project")
@click.option("--global-only", is_flag=True, help="Show only the global profile")
def profile(project, global_only):
    """Show current weighted preference profile (Tier 2)."""
    from synapptic.profile import profile_summary

    if project:
        p = load_profile(project_slug=project)
        if not p.get("dimensions"):
            click.echo(f"No profile for project '{project}'.")
            return
        click.echo(profile_summary(p, label=f"[{project}]"))
    elif global_only:
        p = load_profile(project_slug=None)
        if not p.get("dimensions"):
            click.echo("No global profile yet.")
            return
        click.echo(profile_summary(p, label="[global]"))
    else:
        # Show all
        gp = load_profile(project_slug=None)
        if gp.get("dimensions"):
            click.echo(profile_summary(gp, label="[global]"))

        for slug in list_projects():
            pp = load_profile(project_slug=slug)
            if pp.get("dimensions"):
                click.echo(profile_summary(pp, label=f"[{slug}]"))


@cli.command()
@click.option("--decay", default=0.95, help="Decay factor for existing preferences")
def merge(decay):
    """Merge all observations into global + per-project profiles (Tier 2).

    Uses find_all_observations() to discover observations with their embedded
    project_slug — does not depend on transcript files still existing.
    Only merges sessions not already in the profile's sources.
    """
    from synapptic.profile import merge_observations, route_observations, promote_to_global

    # Collect already-merged session IDs from existing profiles
    all_merged = set()
    for p in [load_profile(project_slug=None)] + [load_profile(project_slug=s) for s in list_projects()]:
        for prefs in p.get("dimensions", {}).values():
            for pref in prefs:
                all_merged.update(pref.get("sources", []))

    # Load all observations, skipping already-merged and empty
    project_observations = {}
    global_observations = []
    new_session_count = 0

    for session_id, slug, obs_list in find_all_observations():
        if session_id in all_merged:
            continue
        new_session_count += 1

        global_obs, project_obs = route_observations(obs_list)
        global_observations.extend(global_obs)

        if slug and project_obs:
            project_observations.setdefault(slug, []).extend(project_obs)

    if not global_observations and not project_observations:
        click.echo("No new observations to merge.")
        return

    click.echo(f"Merging {new_session_count} new session(s)")

    # Merge global
    if global_observations:
        existing = load_profile(project_slug=None)
        updated = merge_observations(existing, global_observations, decay_factor=decay)
        save_profile(updated, project_slug=None)
        n = sum(len(v) for v in updated.get("dimensions", {}).values())
        click.echo(f"Global: v{updated['metadata']['profile_version']}, {n} preferences")

    # Merge per-project
    for slug, obs_list in project_observations.items():
        existing = load_profile(project_slug=slug)
        updated = merge_observations(existing, obs_list, decay_factor=decay)
        save_profile(updated, project_slug=slug)
        n = sum(len(v) for v in updated.get("dimensions", {}).values())
        click.echo(f"[{slug}]: v{updated['metadata']['profile_version']}, {n} preferences")

    # Promote cross-project observations to global
    project_profiles = {slug: load_profile(project_slug=slug) for slug in list_projects()}
    global_profile = load_profile(project_slug=None)
    promotions = promote_to_global(project_profiles, global_profile)
    if promotions:
        click.echo(f"\nPromoting {len(promotions)} cross-project observations to global")
        updated = merge_observations(global_profile, promotions, decay_factor=1.0)
        save_profile(updated, project_slug=None)


@cli.command()
@click.option("--model", default=None, help="Override model (default: from config)")
@click.option("--project", "-p", help="Synthesize only this project")
def synthesize(model, project):
    """Generate narrative archetypes from profiles (Tier 3)."""
    from synapptic.synthesize import synthesize_archetype as do_synthesize
    from synapptic.providers import load_config

    llm_config = load_config()
    if model:
        llm_config["model"] = model

    # Global (always synthesize - it's cheap and keeps the archetype current)
    gp = load_profile(project_slug=None)
    if gp.get("dimensions"):
        click.echo("Synthesizing global archetype...")
        narrative = do_synthesize(gp, config=llm_config)
        if narrative:
            save_archetype(narrative, project_slug=None)
            click.echo(f"  Global archetype: {len(narrative)} chars")
        else:
            click.echo("  Global: skipped (see message above)")

    # Per-project
    slugs = [project] if project else list_projects()
    for slug in slugs:
        pp = load_profile(project_slug=slug)
        if not pp.get("dimensions"):
            continue
        click.echo(f"Synthesizing [{slug}] archetype...")
        narrative = do_synthesize(pp, config=llm_config)
        if narrative:
            save_archetype(narrative, project_slug=slug)
            click.echo(f"  [{slug}]: {len(narrative)} chars")
        else:
            click.echo(f"  [{slug}]: skipped (see message above)")


@cli.command()
@click.option("--project", "-p", help="Show archetype for a specific project")
def archetype(project):
    """Show current narrative archetype (Tier 3)."""
    if project:
        content = load_archetype(project_slug=project)
        if not content:
            click.echo(f"No archetype for project '{project}'.")
            return
        click.echo(f"--- [{project}] ---\n")
        click.echo(content)
    else:
        gc = load_archetype(project_slug=None)
        if gc:
            click.echo("--- [global] ---\n")
            click.echo(gc)
        for slug in list_projects():
            pc = load_archetype(project_slug=slug)
            if pc:
                click.echo(f"\n--- [{slug}] ---\n")
                click.echo(pc)


@cli.command()
@click.option("--project", "-p", help="Benchmark a specific project's guards")
@click.option("--max-guards", "-n", default=10, help="Max guards to test")
@click.option("--model", default=None, help="Override model (default: from config)")
@click.option("--verbose", "-v", is_flag=True, help="Show full prompts, scenarios, and responses")
@click.option("--seed", type=int, default=None, help="Random seed (same seed = same cached tests)")
@click.option("--refresh", is_flag=True, help="Regenerate test cases even if cached")
def benchmark(project, max_guards, model, verbose, seed, refresh):
    """Test whether guards are holding with adversarial scenarios."""
    from synapptic.benchmark import run_benchmark, save_benchmark, format_results
    from synapptic.providers import load_config

    llm_config = load_config()
    if model:
        llm_config["model"] = model

    if seed is None:
        import random
        seed = random.randint(0, 999999)

    click.echo(f"Running benchmark ({max_guards} guards, project={project or 'global'}, seed={seed})...\n")
    results = run_benchmark(project_slug=project, max_guards=max_guards, config=llm_config, verbose=verbose, seed=seed, refresh=refresh)

    if not results:
        return

    click.echo()
    click.echo(format_results(results))

    path = save_benchmark(results)
    click.echo(f"\nSaved to {path}")

    # Offer to exclude backfire guards
    backfires = [t for t in results.get("tests", []) if t["classification"] == "backfire"]
    if backfires:
        click.echo(f"\n{len(backfires)} guard(s) made behavior WORSE:")
        for t in backfires:
            click.echo(f"  !! {t['rule'][:100]}")
        if click.confirm("\nExclude these guards from the archetype?", default=True):
            from synapptic.benchmark import exclude_guards
            excluded = exclude_guards([t["rule"] for t in backfires], "backfire", project_slug=project)
            click.echo(f"  Excluded {excluded} guard(s). Run 'synapptic synthesize' to regenerate.")

    # Offer to exclude redundant guards
    redundants = [t for t in results.get("tests", []) if t["classification"] == "redundant"]
    if redundants:
        click.echo(f"\n{len(redundants)} guard(s) are redundant (AI follows them naturally):")
        for t in redundants:
            click.echo(f"  == {t['rule'][:100]}")
        if click.confirm("\nExclude these to keep the archetype lean?", default=False):
            from synapptic.benchmark import exclude_guards
            excluded = exclude_guards([t["rule"] for t in redundants], "redundant", project_slug=project)
            click.echo(f"  Excluded {excluded} guard(s). Run 'synapptic synthesize' to regenerate.")


@cli.group("guards")
def guards_group():
    """View and manage excluded guards."""
    pass


@guards_group.command("excluded")
@click.option("--project", "-p", help="Project to check")
def guards_excluded(project):
    """List all excluded guards with their reasons."""
    from synapptic.benchmark import list_excluded

    excluded = list_excluded(project_slug=project)
    if not excluded:
        click.echo("No excluded guards.")
        return

    for i, g in enumerate(excluded):
        click.echo(f"  {i}. [{g['reason']:9s}] {g['observation'][:90]}")

    click.echo(f"\n{len(excluded)} excluded guard(s). Use 'synapptic guards include <number>' to re-include.")


@guards_group.command("include")
@click.argument("indices", nargs=-1, type=int)
@click.option("--project", "-p", help="Project to modify")
def guards_include(indices, project):
    """Re-include excluded guards by their index number."""
    from synapptic.benchmark import include_guards

    if not indices:
        click.echo("Specify guard indices to re-include (from 'synapptic guards excluded').")
        return

    included = include_guards(list(indices), project_slug=project)
    click.echo(f"Re-included {included} guard(s). Run 'synapptic synthesize' to regenerate.")


@cli.command()
@click.option("--model", default=None, help="Override model (default: from config)")
@click.option("--max-tokens", default=50_000, help="Max tokens per session after filtering")
@click.option("--project", "-p", help="Only process/integrate this project")
@click.option("--min-lines", default=20, help="Skip transcripts with fewer lines than this")
@click.option("--limit", "-n", default=0, type=int, help="Max sessions to process (0 = all)")
def update(model, max_tokens, project, min_lines, limit):
    """Full pipeline: extract → merge → synthesize → integrate."""
    from synapptic.filter import filter_transcript
    from synapptic.extract import extract_observations
    from synapptic.profile import merge_observations, route_observations, promote_to_global
    from synapptic.synthesize import synthesize_archetype as do_synthesize
    from synapptic.integrate import integrate_archetypes
    from synapptic.providers import load_config

    llm_config = load_config()
    if model:
        llm_config["model"] = model

    # Step 1: Find unprocessed sessions
    transcripts = find_session_transcripts()
    processed = get_processed_session_ids()

    queued = set(get_queue())
    unprocessed = [sid for sid in transcripts if sid not in processed]
    unprocessed = list(dict.fromkeys(unprocessed))

    if project:
        unprocessed = [sid for sid in unprocessed if transcripts[sid][1] == project]

    # Sort by file size descending so the richest sessions get processed first
    unprocessed.sort(key=lambda sid: transcripts[sid][0].stat().st_size, reverse=True)

    total_pending = len(unprocessed)
    click.echo(f"Step 1: {total_pending} unprocessed sessions" + (f" (limit {limit})" if limit else ""))

    # Load profiles for context
    global_profile = load_profile(project_slug=None)
    has_profile = bool(global_profile.get("dimensions"))
    if has_profile:
        n = sum(len(v) for v in global_profile["dimensions"].values())
        click.echo(f"Profile-aware extraction ({n} global preferences)")

    # Step 2: Extract + route
    all_global_obs = []
    all_project_obs = {}  # slug -> list[obs]
    extracted_count = 0

    for sid in unprocessed:
        if limit and extracted_count >= limit:
            break

        path, slug = transcripts[sid]
        size_mb = path.stat().st_size / 1024 / 1024
        click.echo(f"  {sid[:8]}... [{slug}] ({size_mb:.1f}MB)", nl=False)

        line_count = count_lines(path)
        if line_count < min_lines:
            click.echo(f" — skipped ({line_count} lines)")
            save_observations(sid, [], project_slug=slug)
            continue

        turns = filter_transcript(path, max_tokens=max_tokens)
        if not turns:
            click.echo(" — no content")
            save_observations(sid, [], project_slug=slug)
            continue

        project_profile = load_profile(project_slug=slug)
        combined = merge_profiles_for_context(global_profile, project_profile) if has_profile else None

        observations = extract_observations(turns, sid, profile=combined, config=llm_config)

        for obs in observations:
            obs["project"] = slug

        global_obs, project_obs = route_observations(observations)
        all_global_obs.extend(global_obs)
        all_project_obs.setdefault(slug, []).extend(project_obs)

        save_observations(sid, observations, project_slug=slug)
        extracted_count += 1
        click.echo(f" → {len(observations)} obs ({len(global_obs)}g/{len(project_obs)}p)")

    clear_queue()

    # Step 3: Merge
    click.echo(f"\nStep 2: Merging")
    if all_global_obs:
        gp = load_profile(project_slug=None)
        gp = merge_observations(gp, all_global_obs)
        save_profile(gp, project_slug=None)
        n = sum(len(v) for v in gp.get("dimensions", {}).values())
        click.echo(f"  Global: {n} preferences")

    for slug, obs_list in all_project_obs.items():
        pp = load_profile(project_slug=slug)
        pp = merge_observations(pp, obs_list)
        save_profile(pp, project_slug=slug)
        n = sum(len(v) for v in pp.get("dimensions", {}).values())
        click.echo(f"  [{slug}]: {n} preferences")

    # Promote cross-project patterns
    project_profiles = {s: load_profile(project_slug=s) for s in list_projects()}
    gp = load_profile(project_slug=None)
    promotions = promote_to_global(project_profiles, gp)
    if promotions:
        click.echo(f"  Promoted {len(promotions)} observations to global")
        gp = merge_observations(gp, promotions, decay_factor=1.0)
        save_profile(gp, project_slug=None)

    # Step 4: Synthesize
    click.echo("\nStep 3: Synthesizing")
    gp = load_profile(project_slug=None)
    if gp.get("dimensions"):
        narrative = do_synthesize(gp, config=llm_config)
        if narrative:
            save_archetype(narrative, project_slug=None)
            click.echo(f"  Global: {len(narrative)} chars")

    slugs = [project] if project else list_projects()
    for slug in slugs:
        pp = load_profile(project_slug=slug)
        if not pp.get("dimensions"):
            continue
        narrative = do_synthesize(pp, config=llm_config)
        if narrative:
            save_archetype(narrative, project_slug=slug)
            click.echo(f"  [{slug}]: {len(narrative)} chars")

    # Step 5: Integrate
    click.echo("\nStep 4: Integrating")
    updated = integrate_archetypes(project_slugs=[project] if project else None)
    for slug, target, path in updated:
        click.echo(f"  [{slug}] → {target}: {path}")

    click.echo("\nDone.")


@cli.command()
def diff():
    """Show what changed since last profile version."""
    show_diff(None, "global")
    for slug in list_projects():
        show_diff(slug, slug)


@cli.command()
def rollback():
    """Rollback to previous profile version."""
    from synapptic.config import GLOBAL_DIR

    history = get_profile_history(project_slug=None)
    if len(history) < 2:
        click.echo("No previous global version to rollback to.")
        return

    previous = history[1]
    click.echo(f"Rolling back global to: {previous.name}")
    shutil.copy2(previous, GLOBAL_DIR / "profile.yaml")
    click.echo("Done. Run 'synaptic synthesize' to regenerate archetypes.")


@cli.command()
@click.confirmation_option(prompt="Delete all synaptic state and start fresh?")
def reset():
    """Delete all synaptic state and start fresh."""
    reset_all()
    click.echo("All synaptic state deleted. Starting fresh.")


@cli.command()
def stats():
    """Show statistics about processed sessions and profile."""
    transcripts = find_session_transcripts()
    processed = get_processed_session_ids()
    queued = get_queue()

    click.echo(f"Sessions found:      {len(transcripts)}")
    click.echo(f"Sessions processed:  {len(processed)}")
    click.echo(f"Sessions queued:     {len(queued)}")

    # Global
    gp = load_profile(project_slug=None)
    dims = gp.get("dimensions", {})
    total = sum(len(v) for v in dims.values())
    click.echo(f"\n[global] v{gp.get('metadata', {}).get('profile_version', 0)}, {total} preferences")
    for dim, prefs in dims.items():
        click.echo(f"  {dim}: {len(prefs)}")

    ga = load_archetype(project_slug=None)
    click.echo(f"  archetype: {'yes' if ga else 'no'}" + (f" ({len(ga)} chars)" if ga else ""))

    # Per-project
    for slug in list_projects():
        pp = load_profile(project_slug=slug)
        dims = pp.get("dimensions", {})
        total = sum(len(v) for v in dims.values())
        click.echo(f"\n[{slug}] v{pp.get('metadata', {}).get('profile_version', 0)}, {total} preferences")
        for dim, prefs in dims.items():
            click.echo(f"  {dim}: {len(prefs)}")

        pa = load_archetype(project_slug=slug)
        click.echo(f"  archetype: {'yes' if pa else 'no'}" + (f" ({len(pa)} chars)" if pa else ""))

    # Project distribution of sessions
    click.echo(f"\nSessions by project:")
    project_counts = {}
    for sid, (path, slug) in transcripts.items():
        project_counts[slug] = project_counts.get(slug, 0) + 1
    for slug, count in sorted(project_counts.items(), key=lambda x: -x[1]):
        click.echo(f"  {slug}: {count}")


@cli.command("install")
def install_cmd():
    """Set up skill, hook, and settings.json registration."""
    from importlib.resources import files

    bundle = files("synapptic") / "bundle"
    claude_dir = Path.home() / ".claude"
    skills_dir = claude_dir / "skills" / "synapptic"
    hooks_dir = claude_dir / "hooks"
    settings_path = claude_dir / "settings.json"

    # 1. Skill
    click.echo("[1/3] Installing skill...")
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_src = bundle / "skill" / "SKILL.md"
    shutil.copy2(str(skill_src), str(skills_dir / "SKILL.md"))
    click.echo(f"  Done: {skills_dir}/")

    # 2. Hook script
    click.echo("[2/3] Installing SessionEnd hook...")
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_dst = hooks_dir / "synapptic-session-end.sh"
    shutil.copy2(str(bundle / "hooks" / "synapptic-session-end.sh"), str(hook_dst))
    hook_dst.chmod(0o755)
    click.echo(f"  Done: {hook_dst}")

    # 3. Register in settings.json
    click.echo("[3/3] Registering hook in settings.json...")
    settings = load_settings_json(settings_path)
    if settings is None:
        click.echo("  Failed to read settings.json — skipping hook registration")
    else:
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
            settings["hooks"] = hooks
        session_end = hooks.get("SessionEnd")
        if not isinstance(session_end, list):
            session_end = []
            hooks["SessionEnd"] = session_end

        already = any(
            "synapptic-session-end" in h.get("command", "")
            for entry in session_end
            if isinstance(entry, dict)
            for h in entry.get("hooks", [])
        )
        if already:
            click.echo("  Already registered")
        else:
            session_end.append({
                "matcher": "*",
                "hooks": [{
                    "type": "command",
                    "command": "~/.claude/hooks/synapptic-session-end.sh",
                }],
            })
            save_settings_json(settings_path, settings)
            click.echo("  Done")

    ensure_dirs()
    click.echo()
    click.echo("Installed. Run 'synaptic update' to process existing sessions.")


@cli.command("uninstall")
def uninstall_cmd():
    """Remove skill, hook, and settings.json registration. Prompts for artifacts."""
    claude_dir = Path.home() / ".claude"
    skills_dir = claude_dir / "skills" / "synapptic"
    hooks_dir = claude_dir / "hooks"
    settings_path = claude_dir / "settings.json"
    synaptic_state = claude_dir / "synaptic"

    # 1. Remove hook from settings.json
    click.echo("[1/4] Removing hook from settings.json...")
    settings = load_settings_json(settings_path)
    if settings is None:
        click.echo("  Could not read settings.json")
    else:
        hooks = settings.get("hooks", {})
        session_end = hooks.get("SessionEnd", [])
        if isinstance(session_end, list):
            filtered = [
                entry for entry in session_end
                if not isinstance(entry, dict) or not any(
                    "synapptic-session-end" in h.get("command", "")
                    for h in entry.get("hooks", [])
                )
            ]
            if len(filtered) < len(session_end):
                hooks["SessionEnd"] = filtered
                save_settings_json(settings_path, settings)
                click.echo("  Removed")
            else:
                click.echo("  Not found (already clean)")
        else:
            click.echo("  Not found (already clean)")

    # 2. Remove hook script
    click.echo("[2/4] Removing hook script...")
    hook_path = hooks_dir / "synapptic-session-end.sh"
    if hook_path.exists():
        hook_path.unlink()
        click.echo(f"  Removed {hook_path}")
    else:
        click.echo("  Not found (already clean)")

    # 3. Remove skill
    click.echo("[3/4] Removing skill...")
    if skills_dir.exists():
        shutil.rmtree(skills_dir)
        click.echo(f"  Removed {skills_dir}/")
    else:
        click.echo("  Not found (already clean)")

    # 4. Remove generated archetype files + MEMORY.md references
    click.echo("[4/4] Removing generated files...")
    projects_dir = claude_dir / "projects"
    arch_count = 0
    mem_count = 0
    if projects_dir.exists():
        for proj in projects_dir.iterdir():
            if not proj.is_dir():
                continue
            memory_dir = proj / "memory"
            arch_file = memory_dir / "user_archetype.md"
            if arch_file.exists():
                arch_file.unlink()
                arch_count += 1
            mem_file = memory_dir / "MEMORY.md"
            if mem_file.exists():
                content = mem_file.read_text()
                if "user_archetype.md" in content:
                    lines = [
                        l for l in content.splitlines()
                        if not ("user_archetype.md" in l and "Auto-generated user archetype" in l)
                    ]
                    mem_file.write_text("\n".join(lines) + "\n")
                    mem_count += 1

    click.echo(f"  Removed {arch_count} user_archetype.md file(s)")
    click.echo(f"  Cleaned {mem_count} MEMORY.md reference(s)")

    # Artifacts — prompt
    click.echo()
    if synaptic_state.exists():
        obs_count = sum(
            len(list((d / "observations").glob("*.json")))
            for d in [synaptic_state / "global"] + list((synaptic_state / "projects").iterdir())
            if (d / "observations").exists()
        ) if (synaptic_state / "global").exists() else 0

        project_count = len(list((synaptic_state / "projects").iterdir())) if (synaptic_state / "projects").exists() else 0
        hist_count = len(list((synaptic_state / "profile_history").glob("*.yaml"))) if (synaptic_state / "profile_history").exists() else 0

        click.echo("Artifacts created by synaptic:")
        click.echo(f"  {obs_count} session observation(s)")
        click.echo(f"  {project_count} project profile(s)")
        click.echo(f"  {hist_count} history snapshot(s)")
        click.echo()

        if click.confirm("Delete these artifacts? They cannot be recovered", default=False):
            shutil.rmtree(synaptic_state)
            click.echo("  Artifacts deleted")
        else:
            click.echo(f"  Artifacts preserved at {synaptic_state}/")
    else:
        click.echo("No artifacts found.")

    click.echo()
    click.echo("Uninstall complete. Run 'pip uninstall synaptic' to remove the package.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def show_diff(project_slug, label):
    """Show diff for a profile."""
    history = get_profile_history(project_slug=project_slug)
    if len(history) < 2:
        return

    current = yaml.safe_load(history[0].read_text()) or {}
    previous = yaml.safe_load(history[1].read_text()) or {}

    curr_dims = current.get("dimensions", {})
    prev_dims = previous.get("dimensions", {})

    has_changes = False
    for dim in set(list(curr_dims.keys()) + list(prev_dims.keys())):
        curr_obs = {p["observation"] for p in curr_dims.get(dim, [])}
        prev_obs = {p["observation"] for p in prev_dims.get(dim, [])}
        if curr_obs != prev_obs:
            if not has_changes:
                click.echo(f"\n## [{label}] Changes\n")
                has_changes = True
            click.echo(f"### {dim.title()}")
            for obs in curr_obs - prev_obs:
                click.echo(f"  + {obs}")
            for obs in prev_obs - curr_obs:
                click.echo(f"  - {obs}")
            click.echo()


def merge_profiles_for_context(global_profile: dict, project_profile: dict) -> dict:
    """Merge global + project profiles into one for extraction context."""
    combined = {"dimensions": {}, "metadata": global_profile.get("metadata", {})}

    for dim, prefs in global_profile.get("dimensions", {}).items():
        combined["dimensions"][dim] = list(prefs)

    for dim, prefs in project_profile.get("dimensions", {}).items():
        if dim in combined["dimensions"]:
            combined["dimensions"][dim].extend(prefs)
        else:
            combined["dimensions"][dim] = list(prefs)

    return combined


def count_lines(path: Path) -> int:
    """Count lines in a file without loading it all into memory."""
    count = 0
    with open(path, "rb") as f:
        for _ in f:
            count += 1
    return count


def load_settings_json(path: Path) -> dict | None:
    """Load settings.json with error handling. Returns None on failure."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError) as e:
        click.echo(f"  Warning: could not parse {path}: {e}", err=True)
        return None


def save_settings_json(path: Path, settings: dict):
    """Save settings.json with atomic write."""
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    tmp_path.rename(path)

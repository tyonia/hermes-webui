# Provider Manager custom build

This fork keeps provider management in independent Python and browser modules.
The long-lived branch is `codex/provider-manager`; `upstream` is the official
repository and `origin` is the fork. `custom-build.json` identifies this build.

## Controlled upgrades

The marker disables WebUI normal, force, and lock-recovery update operations
before run admission, cancellation, Git mutation, or restart. Version checks
remain available. Agent upgrades keep their existing behavior; record and
verify the compatible Agent revision before upgrading it.

Do not use the official WebUI installer to overwrite this checkout. Do not
delete the build marker or reset the custom branch to an upstream release.

1. Commit all intended changes and push the custom branch to the fork.
2. Record the deployed WebUI/Agent revisions and tag the working custom build.
3. Fetch upstream tags; select an explicit release rather than moving blindly
   to the latest development commit.
4. Merge that release into a candidate branch based on the custom branch.
   Resolve conflicts while retaining the marker and custom module integration.
5. Run `./scripts/test.sh` with disposable Hermes home/state directories.
   Verify provider config round trips, profile ownership, model routing, and
   desktop/mobile settings behavior. Never use production credentials in trials.
6. Merge the tested candidate into the long-lived branch, push it, and deploy
   that exact commit during a planned service restart.
7. If verification/deployment fails, return to the recorded custom-build tag.
   Back up profile configuration separately before any config migration; a Git
   rollback does not undo data changes. This editor requires no migration.

The guard prevents browser-initiated checkout replacement, not arbitrary shell
commands or external installers. Preservation of commits does not prove runtime
compatibility with a new Agent or provider API.

## Implementation boundaries

- Stage 1: fork, custom branch, controlled update guard and regression tests.
- Stage 2: source-preserving profile config adapter and API.
- Stage 3: provider/model split editor, discovery and integration checks.
- Deployment is separate: review the commits and isolated acceptance evidence,
  then explicitly authorize any restart of an existing service or real-state
  validation. No profile migration is needed to enable the editor.

The interface follows the provider/model editing workflow in agegr/pi-web
(MIT), adapted to Hermes's existing Python and vanilla JavaScript architecture.
It does not depend on a running pi-web instance or write pi configuration.

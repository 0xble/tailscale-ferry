# Contributing

## Development

Install Go 1.27.1, Python 3.9 or newer, Git, make and a C compiler. macOS and Linux are
supported. Tailscale, a personal account, Docker and maintainer tools are not
required for contributor checks.

```sh
./bin/ci           # download modules, then vet, test, build, race and harness checks
./bin/ci setup     # prepare checkout-local Go modules separately
./bin/ci check     # run checks with prepared modules
./bin/check        # compatible spelling for the complete gate
make build        # build bin/ferry and bin/ferryd
bin/doctor        # optional diagnostics for an actual installed ferry service
```

Run the entrypoint in the requested checkout or use its absolute path. Linked
worktrees have separate `.ci` module/build caches and private invocation homes.
CI ignores personal Go, Git and Tailscale configuration. A checkout lock rejects
simultaneous checks in the same worktree. Different worktrees can run concurrently.
Cancellation terminates command process groups. Source or index changes fail the
gate and remain available for inspection. CI does not install Git hooks.

The optional formatting hook can be installed for this worktree after checking
for an existing hook configuration:

```sh
git config --show-origin --get core.hooksPath
git config extensions.worktreeConfig true
git config --worktree core.hooksPath git-hooks
```

Preserve existing hooks rather than overwriting their configuration. There is no
full pre-push gate. Run the complete gate before requesting review.

## Browser regression lane

The separate browser lane checks the actual preview layout in Chromium and
WebKit across fourteen browser/size combinations. It uses synthetic routed HTML,
without a running ferry or Tailscale account. It remains explicit rather than
being silently claimed by the default Go gate.

```sh
./bin/ci setup
./bin/ci browser-setup  # private Python environment and pinned Playwright browsers
./bin/ci browser
```

Linux hosts additionally need Playwright's system browser libraries. Browser
installation is an explicit preparation step and never runs from `check`.
Screenshots are not written to shared `/tmp` paths.

## Pull requests and releases

Use focused changes and Conventional Commit messages. All required checks run
locally. An independently installed operator can execute `bin/ci` in its sandbox
and publish the exact PR commit's `local-ci/full` result. GitHub's normal merge
path enforces that result. Contributors do not need the operator or its credentials.
There is no GitHub Actions workflow or custom merge command.

Maintainers retain `.goreleaser.yaml` as the release packaging contract: both
binaries, Darwin/Linux on arm64/amd64, tar archives, checksums and draft releases.
Release binaries use pure-Go SQLite with CGO disabled for portable cross-builds.
The native race gate still enables CGO.
To prepare a release without publishing, run the gate and then
`goreleaser release --snapshot --clean --skip=publish` using GoReleaser v2.
For an explicitly authorized draft release, create and push the intended version
tag separately, then run `bin/release --draft vX.Y.Z` with existing `gh` and
GoReleaser authentication. The command checks clean source, local/remote tag
identity and runs the full gate before packaging. It creates only a draft under
the existing GoReleaser configuration. Publishing that draft is a separate
maintainer decision. CI migration does not publish a release or activate ferryd.

Report bugs at https://github.com/0xble/tailscale-ferry/issues with reproducible
steps and relevant diagnostics. See [SECURITY.md](SECURITY.md) for security issues.

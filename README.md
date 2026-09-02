# homebrew-tap

## Agent Forge Worker

```sh
brew install 0k-lab/tap/agent-forge
```

This installs `forge-worker`, `forge-codex-plugin`, and `forge-ref-plugin`.
It is a Worker-only package and does not install the Gate service.

## Bump Agent Forge

1. Run the **Bump Agent Forge Formula** workflow on `main` with an exact stable tag such as `v1.2.3`.
2. Open the created pull request and wait for its explicitly dispatched **Agent Forge bottles** run to pass on both macOS architectures at the exact PR head.
3. Merge the pull request manually only after that exact-head run is green. The automation never merges or enables auto-merge.

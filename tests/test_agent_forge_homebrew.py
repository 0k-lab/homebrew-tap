import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMULA = ROOT / "Formula/agent-forge.rb"
WORKFLOW = ROOT / ".github/workflows/agent-forge-bottles.yml"
README = ROOT / "README.md"

VERSION = "0.1.7"
TAG = f"v{VERSION}"
COMMIT = "ee034b53b5af660cfca66985959b566ecfdd0f73"
SOURCE_SHA256 = "bec4fb1a01935d2df21166aca4d6ef7207b35fa1ecade07ca53998918f941596"
ROOT_URL = "https://github.com/0k-lab/homebrew-tap/releases/download/agent-forge-v0.1.7"
BINARIES = {
    "forge-worker": "./cmd/forge-worker",
    "forge-codex-plugin": "./cmd/forge-codex-plugin",
    "forge-ref-plugin": "./cmd/forge-ref-plugin",
}


class FormulaContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = FORMULA.read_text()

    def test_exact_upstream_identity(self):
        for line in (
            'homepage "https://github.com/0k-lab/agent-forge"',
            f'url "https://github.com/0k-lab/agent-forge/archive/refs/tags/{TAG}.tar.gz"',
            f'sha256 "{SOURCE_SHA256}"',
        ):
            self.assertIn(line, self.text)
        self.assertRegex(self.text, re.compile(r'^  desc "[^"\n]+"$', re.MULTILINE))
        self.assertNotRegex(self.text, re.compile(r'^\s*version\s+', re.MULTILINE))

    def test_worker_only_dependencies_and_build_environment(self):
        self.assertIn("depends_on :macos", self.text)
        self.assertIn('depends_on "go" => :build', self.text)
        for key, value in {
            "CGO_ENABLED": "0",
            "GOFLAGS": "-mod=readonly",
            "GOENV": "off",
            "GOWORK": "off",
            "GOEXPERIMENT": "",
            "GOFIPS140": "off",
        }.items():
            self.assertIn(f'ENV["{key}"] = "{value}"', self.text)

    def test_exact_binary_packages_and_reproducible_identity(self):
        pairs = dict(re.findall(r'^      "([\w-]+)" => "(\./cmd/[\w-]+)",$', self.text, re.MULTILINE))
        self.assertEqual(BINARIES, pairs)
        self.assertIn('"-trimpath"', self.text)
        self.assertIn('"-buildvcs=false"', self.text)
        self.assertIn('"-buildid="', self.text)
        self.assertIn('"-X", "agent-forge/internal/buildinfo.Version=v0.1.7"', self.text)
        self.assertIn(f'"-X", "agent-forge/internal/buildinfo.Commit={COMMIT}"', self.text)
        self.assertEqual(1, self.text.count('system "go", "build"'))
        self.assertIn('"-o", bin/name', self.text)

    def test_exact_versions_and_no_forbidden_surface(self):
        self.assertIn(f'commit = "{COMMIT}"', self.text)
        for binary in BINARIES:
            self.assertIn(f'assert_equal "{binary} {TAG} #{{commit}}", shell_output("#{{bin}}/{binary} --version").strip', self.text)
        lowered = self.text.lower()
        for forbidden in ("bottle do", "service do", "license ", "forge-gate", "cask", "notar", "pkgshare", "etc.install", "var/"):
            self.assertNotIn(forbidden, lowered)


class WorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text()

    def test_safe_triggers_permissions_and_matrix(self):
        self.assertRegex(self.text, r'(?m)^"on":\n  pull_request:\n  push:\n    branches: \[main\]$')
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read$")
        self.assertIn("fail-fast: false", self.text)
        self.assertRegex(self.text, r"runner: macos-14\n\s+arch: arm64")
        self.assertRegex(self.text, r"runner: macos-15-intel\n\s+arch: x86_64")
        for forbidden in ("workflow_run", "self-hosted", "contents: write", "pull-requests: write", "secrets."):
            self.assertNotIn(forbidden, self.text)

    def test_only_pinned_reviewed_actions(self):
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)", self.text)
        self.assertEqual(
            [
                "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
                "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            ],
            uses,
        )
        self.assertNotIn("setup-", self.text)

    def test_local_tap_source_build_and_identity(self):
        for required in (
            'ln -s "$GITHUB_WORKSPACE" "$tap_dir"',
            'test "$(uname -m)" = "${{ matrix.arch }}"',
            "brew audit --strict 0k-lab/tap/agent-forge",
            "brew install --build-bottle 0k-lab/tap/agent-forge",
            "brew test 0k-lab/tap/agent-forge",
        ):
            self.assertIn(required, self.text)
        for forbidden in ("brew tap ", "git clone", "git fetch"):
            self.assertNotIn(forbidden, self.text)
        for binary in BINARIES:
            self.assertGreaterEqual(self.text.count(f'{binary} {TAG} {COMMIT}'), 2)

    def test_bottle_is_bounded_uploaded_and_proven_poured(self):
        for required in (
            f'brew bottle --json --root-url="{ROOT_URL}" 0k-lab/tap/agent-forge',
            'test "${#bottles[@]}" -eq 1',
            'test "${#json_files[@]}" -eq 1',
            'brew install --force-bottle "${bottles[0]}"',
            'poured_from_bottle',
            'name: agent-forge-bottle-${{ matrix.arch }}',
            'retention-days: 7',
            'if-no-files-found: error',
            'path: |',
            '${{ steps.bottle.outputs.tar }}',
            '${{ steps.bottle.outputs.json }}',
        ):
            self.assertIn(required, self.text)


class ReadmeContract(unittest.TestCase):
    def test_install_and_scope(self):
        text = README.read_text()
        self.assertIn("brew install 0k-lab/tap/agent-forge", text)
        for binary in BINARIES:
            self.assertIn(f"`{binary}`", text)
        self.assertIn("Worker-only", text)
        self.assertIn("does not install the Gate service", text)


if __name__ == "__main__":
    unittest.main()

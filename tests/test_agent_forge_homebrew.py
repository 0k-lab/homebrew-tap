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
ROOT_URL = "https://github.com/0k-lab/homebrew-tap/releases/download/agent-forge-v0.1.7-1"
ARM64_BOTTLE_SHA256 = "ccb029de9cd5c57171dab3b3c8e8ac4d1b4128264a132f963139b0cd311926a8"
INTEL_BOTTLE_SHA256 = "32f3e8460d4e02af444b77ef5e4354d233a1144e3fa74cf89e387b72d824558d"
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
        for forbidden in ("service do", "license ", "forge-gate", "cask", "notar", "pkgshare", "etc.install", "var/"):
            self.assertNotIn(forbidden, lowered)

    def test_exact_published_bottles(self):
        for required in (
            "bottle do",
            f'root_url "{ROOT_URL}"',
            f'sha256 cellar: :any_skip_relocation, arm64_sonoma: "{ARM64_BOTTLE_SHA256}"',
            f'sha256 cellar: :any_skip_relocation, sequoia: "{INTEL_BOTTLE_SHA256}"',
        ):
            self.assertIn(required, self.text)


class WorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text()

    def test_safe_triggers_permissions_and_matrix(self):
        self.assertRegex(self.text, r'(?m)^"on":\n  pull_request:\n  push:\n    branches: \[main\]\n  workflow_dispatch:$')
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read$")
        self.assertIn("fail-fast: false", self.text)
        self.assertRegex(self.text, r"runner: macos-15\n\s+arch: arm64")
        self.assertRegex(self.text, r"runner: macos-15-intel\n\s+arch: x86_64")
        for forbidden in ("workflow_run", "self-hosted", "contents: write", "pull-requests: write", "secrets."):
            self.assertNotIn(forbidden, self.text)

    def test_only_pinned_reviewed_actions(self):
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)", self.text)
        self.assertEqual(
            ["actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"] * 2,
            uses,
        )
        self.assertNotIn("setup-", self.text)

    def test_remote_bottle_install_and_identity(self):
        for required in (
            'ln -s "$GITHUB_WORKSPACE" "$tap_dir"',
            'test "$(uname -m)" = "${{ matrix.arch }}"',
            "brew audit --strict 0k-lab/tap/agent-forge",
            "brew install --force-bottle 0k-lab/tap/agent-forge",
            "brew test 0k-lab/tap/agent-forge",
            'install["poured_from_bottle"] == true',
        ):
            self.assertIn(required, self.text)
        for forbidden in ("brew tap ", "git clone", "git fetch"):
            self.assertNotIn(forbidden, self.text)
        for binary in BINARIES:
            self.assertIn(f'{binary} ${{{{ steps.identity.outputs.tag }}}} ${{{{ steps.identity.outputs.commit }}}}', self.text)
        self.assertNotIn(f"{TAG} {COMMIT}", self.text)

    def test_no_build_or_publish_surface(self):
        for forbidden in (
            "--build-bottle",
            "brew bottle",
            "upload-artifact",
            "download-artifact",
            "publish:",
            "gh release",
        ):
            self.assertNotIn(forbidden, self.text)


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

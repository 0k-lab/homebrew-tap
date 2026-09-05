import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMULA = ROOT / "Formula/agent-forge.rb"
WORKFLOW = ROOT / ".github/workflows/agent-forge-bottles.yml"
README = ROOT / "README.md"

def formula_contract_identity(text):
    bottle_markers = re.findall(r'^[ \t]*bottle[ \t]+do[ \t]*$', text, re.MULTILINE)
    if bottle_markers != ["  bottle do"]:
        raise ValueError("expected one canonical bottle block")
    bottle_start = text.index("  bottle do\n")
    bottle_end = text.index("  end\n", bottle_start)
    preamble = text[:bottle_start]
    bottle_block = text[bottle_start:bottle_end]

    def exact_raw(scope, pattern, canonical, label):
        raw = re.findall(pattern, scope, re.MULTILINE)
        if len(raw) != 1:
            raise ValueError(f"expected exactly one raw {label} directive")
        match = re.fullmatch(canonical, raw[0])
        if match is None:
            raise ValueError(f"malformed {label} directive")
        return match.groups()

    tag, = exact_raw(text, r'^[ \t]*url(?:[ \t]|\().*$',
        r'  url "https://github\.com/0k-lab/agent-forge/archive/refs/tags/(v[0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"',
        "source URL")
    source_sha, = exact_raw(preamble, r'^[ \t]*sha256(?:[ \t]|\().*$',
        r'  sha256 "([0-9a-f]{64})"', "source SHA-256")
    build_tag, = exact_raw(text, r'^.*agent-forge/internal/buildinfo\.Version=.*$',
        r'      "-X", "agent-forge/internal/buildinfo\.Version=(v[0-9]+\.[0-9]+\.[0-9]+)",',
        "build version")
    build_commit, = exact_raw(text, r'^.*agent-forge/internal/buildinfo\.Commit=.*$',
        r'      "-X", "agent-forge/internal/buildinfo\.Commit=([0-9a-f]{40})",',
        "build commit")
    test_commit, = exact_raw(text, r'^[ \t]*commit[ \t]*=.*$',
        r'    commit = "([0-9a-f]{40})"', "test commit")
    root_url, = exact_raw(bottle_block, r'^[ \t]*root_url(?:[ \t]|\().*$',
        r'    root_url "([^"]+)"', "bottle root URL")
    if build_tag != tag or build_commit != test_commit:
        raise ValueError("Formula identity fields disagree")
    if re.fullmatch(
        rf"https://github\.com/0k-lab/homebrew-tap/releases/download/agent-forge-{re.escape(tag)}(?:-[1-9][0-9]*)?",
        root_url,
    ) is None:
        raise ValueError("bottle root URL does not match Formula version")

    raw_bottles = re.findall(r'^[ \t]*sha256(?:[ \t]|\().*$', bottle_block, re.MULTILINE)
    if len(raw_bottles) != 2:
        raise ValueError("expected exactly two raw bottle SHA-256 directives")
    bottles = {}
    for line in raw_bottles:
        match = re.fullmatch(
            r'    sha256 cellar: :any_skip_relocation, ([a-z0-9_]+): "([0-9a-f]{64})"', line)
        if match is None or match.group(1) in bottles:
            raise ValueError("malformed or duplicate bottle SHA-256 directive")
        bottles[match.group(1)] = match.group(2)
    if set(bottles) not in ({"arm64_sonoma", "sequoia"}, {"arm64_sequoia", "sequoia"}):
        raise ValueError("unsupported bottle tag pair")
    return {"tag": tag, "version": tag[1:], "commit": build_commit,
            "source_sha256": source_sha, "root_url": root_url, "bottles": bottles}


FORMULA_TEXT = FORMULA.read_text()
IDENTITY = formula_contract_identity(FORMULA_TEXT)
TAG = IDENTITY["tag"]
VERSION = IDENTITY["version"]
COMMIT = IDENTITY["commit"]
SOURCE_SHA256 = IDENTITY["source_sha256"]
ROOT_URL = IDENTITY["root_url"]

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
        self.assertIn(f'"-X", "agent-forge/internal/buildinfo.Version={TAG}"', self.text)
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
        parsed = formula_contract_identity(self.text)
        self.assertEqual(parsed, IDENTITY)
        self.assertEqual(2, len(parsed["bottles"]))

    def test_malformed_extra_identity_and_bottle_directives_are_rejected(self):
        mutations = (
            lambda text: text.replace("\n  sha256 ", '\n   url("https://evil.invalid/source.tar.gz")\n  sha256 ', 1),
            lambda text: text.replace("\n\n  bottle do", '\n   sha256("xyz")\n\n  bottle do', 1),
            lambda text: text.replace("internal/buildinfo.Version=", "internal/buildinfo.Version=xyz\n      # agent-forge/internal/buildinfo.Version=", 1),
            lambda text: text.replace("internal/buildinfo.Commit=", "internal/buildinfo.Commit=xyz\n      # agent-forge/internal/buildinfo.Commit=", 1),
            lambda text: text.replace('    commit = "', '     commit = "xyz"\n    commit = "', 1),
            lambda text: text.replace('    root_url "', '     root_url("https://evil.invalid")\n    root_url "', 1),
            lambda text: text.replace("  end\n", '    sha256(cellar: :any_skip_relocation, ventura: "' + "1" * 64 + '")\n  end\n', 1),
            lambda text: text.replace("  end\n", '    sha256 cellar: :any, ventura: "' + "1" * 64 + '"\n  end\n', 1),
        )
        for mutate in mutations:
            changed = mutate(self.text)
            self.assertNotEqual(changed, self.text)
            with self.subTest(), self.assertRaises(ValueError):
                formula_contract_identity(changed)



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

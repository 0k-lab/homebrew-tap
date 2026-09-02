import copy
import hashlib
import io
import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError


ROOT = Path(__file__).resolve().parents[1]
BUMP_WORKFLOW = ROOT / ".github/workflows/agent-forge-formula-bump.yml"
VERIFY_WORKFLOW = ROOT / ".github/workflows/agent-forge-bottles.yml"
SCRIPT = ROOT / "scripts/agent_forge_bump.py"
FORMULA = ROOT / "Formula/agent-forge.rb"
COMMIT = "2c03fd4c797ead5dd10cd786e9f7a0ea8a702e8f"
BASE = "95e16a1df7afba1536c883de14e76a9b264bc9f1"
FORMULA_SHA = "a" * 64
ROOT_URL = "https://github.com/0k-lab/homebrew-tap/releases/download/agent-forge-v0.1.8"
KEY = "0k-lab/tap/agent-forge"
EXEC_FILES = ["bin/forge-codex-plugin", "bin/forge-worker", "bin/forge-ref-plugin"]

spec = importlib.util.spec_from_file_location("agent_forge_bump", SCRIPT)
bump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump)


class UniqueBaseLoader(yaml.BaseLoader):
    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ConstructorError("mapping", node.start_mark, f"duplicate key: {key}", key_node.start_mark)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load_workflow(path):
    return yaml.load(Path(path).read_text(), Loader=UniqueBaseLoader)


def steps(job):
    return {step.get("name"): step for step in job["steps"]}


def executable(run):
    return [command for line in run.splitlines()
            if (command := line.strip().split(" #", 1)[0]) and not command.startswith("#")]


def source_tar(version="0.1.8", module="agent-forge", missing=()):
    data = io.BytesIO()
    root = f"agent-forge-{version}"
    files = {f"{root}/go.mod": f"module {module}\n\ngo 1.25\n".encode()}
    files.update({f"{root}/cmd/{name}/main.go": b"package main\n" for name in bump.BINARIES})
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name, contents in files.items():
            if name in missing:
                continue
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
    return data.getvalue()


def bottle_fixture(directory, tag="arm64_sequoia", arch="arm64", version="0.1.8"):
    local = f"agent-forge--{version}.{tag}.bottle.tar.gz"
    remote = f"agent-forge-{version}.{tag}.bottle.tar.gz"
    tar_path = directory / local
    with tarfile.open(tar_path, "w:gz") as archive:
        for binary in bump.BINARIES:
            contents = f"#!/bin/sh\nprintf '%s\\n' '{binary} v{version} {COMMIT}'\n".encode()
            member = tarfile.TarInfo(f"agent-forge/{version}/bin/{binary}")
            member.mode = 0o755
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    data = {
        KEY: {
            "formula": {
                "name": "agent-forge", "pkg_version": version,
                "path": "Library/Taps/0k-lab/homebrew-tap/Formula/agent-forge.rb",
                "tap_git_path": "Formula/agent-forge.rb", "tap_git_revision": BASE,
                "tap_git_remote": "https://github.com/0k-lab/homebrew-tap",
                "desc": "Worker runtime and plugins for Agent Forge", "license": None,
                "homepage": "https://github.com/0k-lab/agent-forge",
            },
            "bottle": {
                "root_url": ROOT_URL, "cellar": "any_skip_relocation", "rebuild": 0,
                "date": "2026-09-02T00:00:00Z",
                "tags": {tag: {
                    "filename": remote, "local_filename": local, "sha256": digest,
                    "tab": {"homebrew_version": "6.0.18", "changed_files": [],
                            "source_modified_time": 1788307200, "compiler": "clang",
                            "runtime_dependencies": [], "arch": arch,
                            "built_on": {"os": "Macintosh", "os_version": "macOS 15.7"}},
                    "sbom": {"documentDescribes": ["SPDXRef-Compiler"]},
                    "path_exec_files": EXEC_FILES,
                    "all_files": [".brew/agent-forge.rb", "INSTALL_RECEIPT.json", "README.md",
                                  *EXEC_FILES, "sbom.spdx.json"], "installed_size": 123456,
                }},
            },
        }
    }
    json_path = directory / f"agent-forge--{version}.{tag}.bottle.json"
    json_path.write_text(json.dumps(data))
    return data, json_path, tar_path


def add_tar_file(path, name, contents=b"#!/bin/sh\nexit 0\n", mode=0o755):
    original = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.isfile():
                original.append((member.name, archive.extractfile(member).read(), member.mode))
    with tarfile.open(path, "w:gz") as archive:
        for member_name, data, member_mode in [*original, (name, contents, mode)]:
            member = tarfile.TarInfo(member_name)
            member.mode, member.size = member_mode, len(data)
            archive.addfile(member, io.BytesIO(data))


class SourceArchiveTests(unittest.TestCase):
    def test_real_shape_without_version_passes(self):
        bump.inspect_source_archive(source_tar(), "0.1.8")

    def test_malformed_source_identity_fails_closed(self):
        root = "agent-forge-0.1.8"
        cases = (source_tar(module="evil.example/module"),
                 source_tar(missing={f"{root}/go.mod"}),
                 source_tar(missing={f"{root}/cmd/forge-worker/main.go"}),
                 source_tar(version="0.1.9"))
        for archive in cases:
            with self.subTest(), self.assertRaises(bump.ContractError):
                bump.inspect_source_archive(archive, "0.1.8")

    def test_upstream_release_requires_immutable_but_tap_state_does_not(self):
        script = SCRIPT.read_text()
        self.assertIn('release.get("immutable") is True', script)
        self.assertNotIn('immutable-releases', script)
        self.assertNotIn('release.get("immutable") is True', script[script.index("def plan_state"):])
        self.assertIn('"X-GitHub-Api-Version": "2022-11-28"', script)


class FormulaAndBottleTests(unittest.TestCase):
    def test_formula_accepts_old_or_new_exact_tag_pair_only(self):
        old = FORMULA.read_text()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "formula.rb"
            path.write_text(old)
            bump.formula_identity(path)
            new = old.replace("arm64_sonoma", "arm64_sequoia")
            path.write_text(new)
            bump.formula_identity(path)
            bad = new.replace("arm64_sequoia", "arm64_sonoma").replace("sequoia:", "ventura:")
            path.write_text(bad)
            with self.assertRaises(bump.ContractError):
                bump.formula_identity(path)
            path.write_text(new.replace("  end\n", '    sha256 cellar: :any_skip_relocation, ventura: "' + "1" * 64 + '"\n  end\n', 1))
            with self.assertRaises(bump.ContractError):
                bump.formula_identity(path)

    def test_full_tap_qualified_json_is_preserved_and_local_name_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            original, _, _ = bottle_fixture(directory)
            output = directory / "out"
            args = type("Args", (), {"directory": directory, "version": "0.1.8",
                                      "tag": "arm64_sequoia", "root_url": ROOT_URL,
                                      "commit": COMMIT, "output": output})
            bump.validate_bottle(args)
            saved = json.loads((output / "agent-forge-0.1.8.arm64_sequoia.bottle.json").read_text())
            expected = copy.deepcopy(original)
            expected[KEY]["bottle"]["tags"]["arm64_sequoia"]["local_filename"] = \
                "agent-forge-0.1.8.arm64_sequoia.bottle.tar.gz"
            self.assertEqual(saved, expected)

    def test_bottle_rejects_malformed_identity_and_payload(self):
        mutations = (
            lambda data: {"agent-forge": data[KEY]},
            lambda data: (data[KEY]["formula"].__setitem__("path", "Formula/agent-forge.rb") or data),
            lambda data: (data[KEY]["formula"].__setitem__("homepage", "https://evil.invalid") or data),
            lambda data: (data[KEY]["bottle"].__setitem__("rebuild", 1) or data),
            lambda data: (data[KEY]["bottle"]["tags"]["arm64_sequoia"]["tab"].__setitem__("arch", "x86_64") or data),
            lambda data: (data[KEY]["bottle"]["tags"]["arm64_sequoia"].__setitem__("path_exec_files", EXEC_FILES[:-1]) or data),
            lambda data: (data[KEY]["bottle"]["tags"]["arm64_sequoia"].__setitem__(
                "all_files", [name for name in data[KEY]["bottle"]["tags"]["arm64_sequoia"]["all_files"]
                              if name != "bin/forge-worker"]) or data),
        )
        for mutate in mutations:
            with self.subTest(), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                data, json_path, _ = bottle_fixture(directory)
                json_path.write_text(json.dumps(mutate(copy.deepcopy(data))))
                args = type("Args", (), {"directory": directory, "version": "0.1.8",
                                          "tag": "arm64_sequoia", "root_url": ROOT_URL,
                                          "commit": COMMIT, "output": directory / "out"})
                with self.assertRaises(bump.ContractError):
                    bump.validate_bottle(args)

    def test_bottle_rejects_a_fourth_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data, json_path, tar_path = bottle_fixture(directory)
            add_tar_file(tar_path, "agent-forge/0.1.8/bin/extra")
            tag_data = data[KEY]["bottle"]["tags"]["arm64_sequoia"]
            tag_data["sha256"] = hashlib.sha256(tar_path.read_bytes()).hexdigest()
            tag_data["all_files"].append("bin/extra")
            json_path.write_text(json.dumps(data))
            args = type("Args", (), {"directory": directory, "version": "0.1.8",
                                      "tag": "arm64_sequoia", "root_url": ROOT_URL,
                                      "commit": COMMIT, "output": directory / "out"})
            with self.assertRaises(bump.ContractError):
                bump.validate_bottle(args)

    def test_render_final_consumes_full_json_and_new_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source, metadata = directory / "source.rb", directory / "upstream.json"
            metadata.write_text(json.dumps({"commit": COMMIT,
                "source_sha256": "952fd96e058f48d5464d8528202024d77d20948c0c82b43c835b63061bf56032",
                "source_url": "https://github.com/0k-lab/agent-forge/archive/refs/tags/v0.1.8.tar.gz",
                "tag": "v0.1.8", "version": "0.1.8"}))
            bump.prepare_formula(type("Args", (), {"formula": FORMULA, "metadata": metadata, "output": source}))
            artifacts = directory / "artifacts"
            for tag, arch in (("arm64_sequoia", "arm64"), ("sequoia", "x86_64")):
                work = directory / f"work-{tag}"
                work.mkdir()
                bottle_fixture(work, tag, arch)
                bump.validate_bottle(type("Args", (), {"directory": work, "version": "0.1.8",
                    "tag": tag, "root_url": ROOT_URL, "commit": COMMIT, "output": artifacts / tag}))
            output, manifest = directory / "final.rb", directory / "manifest.json"
            bump.render_final(type("Args", (), {"formula": source, "artifacts": artifacts,
                                                 "output": output, "metadata": manifest}))
            self.assertIn("arm64_sequoia", output.read_text())
            self.assertNotIn("arm64_sonoma", output.read_text())
            self.assertEqual(set(json.loads(manifest.read_text())), {"arm64_sequoia", "sequoia"})


class StateMachineTests(unittest.TestCase):
    def expected(self):
        return {"base_sha": BASE, "branch": "bump/agent-forge-v0.1.8",
                "branch_sha": "b" * 40, "formula_sha256": FORMULA_SHA,
                "title": "agent-forge v0.1.8",
                "body": f"Upstream: v0.1.8\nCommit: {COMMIT}\n",
                "release": {"tag_name": "agent-forge-v0.1.8", "target_commitish": BASE,
                            "name": "Agent Forge v0.1.8 bottles",
                            "body": "Homebrew bottles for Agent Forge v0.1.8."},
                "assets": {"one": {"size": 3, "digest": "sha256:" + "1" * 64}}}

    def valid_branch(self):
        return {"sha": "b" * 40, "parent_sha": BASE,
                "files": ["Formula/agent-forge.rb"], "formula_sha256": FORMULA_SHA}

    def test_fresh_and_partial_state_plans_are_resumable(self):
        expected = self.expected()
        fresh = bump.plan_state(expected, {"branch": None, "prs": [], "release": None})
        self.assertEqual((fresh["branch_action"], fresh["pr_action"], fresh["release_action"]),
                         ("create", "create", "create_draft"))
        state = {"branch": self.valid_branch(),
            "prs": [{"number": 18, "url": "https://github.com/0k-lab/homebrew-tap/pull/18",
                     "state": "OPEN", "isDraft": False, "baseRefName": "main",
                     "headRefName": expected["branch"], "headRefOid": "b" * 40,
                     "title": expected["title"], "body": expected["body"]}],
            "release": {**expected["release"], "id": 42, "draft": True, "prerelease": False,
                        "assets": [{"id": 7, "name": "one", "size": 3,
                                    "digest": "sha256:" + "1" * 64}]}}
        resumed = bump.plan_state(expected, state)
        self.assertEqual((resumed["branch_action"], resumed["pr_action"], resumed["release_action"]),
                         ("reuse", "reuse", "publish"))
        self.assertEqual((resumed["pr_number"], resumed["release_id"]), (18, 42))

    def test_public_exact_state_is_complete_despite_immutable_false(self):
        expected = self.expected()
        state = {"branch": self.valid_branch(),
                 "prs": [{"number": 18, "url": "u", "state": "OPEN", "isDraft": False,
                          "baseRefName": "main", "headRefName": expected["branch"],
                          "headRefOid": "b" * 40, "title": expected["title"],
                          "body": expected["body"]}],
                 "release": {**expected["release"], "id": 42, "draft": False,
                             "prerelease": False, "immutable": False,
                             "assets": [{"id": 7, "name": "one", "size": 3,
                                         "digest": "sha256:" + "1" * 64}]},
                 "release_tag_sha": BASE}
        self.assertEqual(bump.plan_state(expected, state)["release_action"], "complete")

    def test_every_conflicting_state_fails_closed(self):
        expected, valid = self.expected(), self.valid_branch()
        conflicts = (
            {"branch": {**valid, "parent_sha": "c" * 40}, "prs": [], "release": None},
            {"branch": {**valid, "files": ["README.md"]}, "prs": [], "release": None},
            {"branch": valid, "prs": [{"state": "CLOSED"}], "release": None},
            {"branch": valid, "prs": [{"number": 18, "url": "u", "state": "OPEN",
                "isDraft": False, "baseRefName": "main", "headRefName": expected["branch"],
                "headRefOid": "b" * 40, "title": expected["title"],
                "body": expected["body"] + "UNEXPECTED CONTENT"}], "release": None},
            {"branch": valid, "prs": [], "release": {**expected["release"], "id": 1,
                "draft": False, "prerelease": False, "assets": []}},
            {"branch": valid, "prs": [], "release": {**expected["release"], "id": 1,
                "draft": True, "prerelease": False,
                "assets": [{"name": "evil", "size": 1, "digest": "sha256:" + "2" * 64}]}},
            {"branch": valid, "prs": [], "release": None, "release_tag_sha": BASE})
        for state in conflicts:
            with self.subTest(), self.assertRaises(bump.ContractError):
                bump.plan_state(expected, state)

    def test_dispatch_reuses_exact_head_or_requires_one_dispatch(self):
        runs = [{"databaseId": 9, "url": "u", "workflowName": "Agent Forge bottles",
                 "event": "workflow_dispatch", "headBranch": "bump/agent-forge-v0.1.8",
                 "headSha": "b" * 40}]
        self.assertEqual(bump.find_dispatch_run(runs, "bump/agent-forge-v0.1.8", "b" * 40)["databaseId"], 9)
        self.assertIsNone(bump.find_dispatch_run(runs, "bump/agent-forge-v0.1.8", "c" * 40))
        runs.append({**runs[0], "databaseId": 10, "url": "new"})
        self.assertEqual(bump.find_dispatch_run(runs, "bump/agent-forge-v0.1.8", "b" * 40)["databaseId"], 10)


class WorkflowContractTests(unittest.TestCase):
    critical = {
        "Inspect resumable branch, PR, and release state":
            ("git ls-remote --heads", "gh pr list", "releases?per_page=100", "state-plan"),
        "Create or validate exact bump branch": ("git push origin", "git ls-remote --heads"),
        "Create or validate exact non-draft pull request": ("gh pr create", "gh pr view", "headRefOid"),
        "Create or resume draft bottle release": ("gh release create", "gh release upload"),
        "Read back exact draft assets":
            ("releases?per_page=100", "state-plan", "releases/assets/$asset_id", "cmp "),
        "Publish exact draft release":
            ("--method PATCH", "releases/$RELEASE_ID", "gh api", "state-plan"),
        "Dispatch or reuse exact-head verification":
            ("gh run list", "gh workflow run agent-forge-bottles.yml", "sleep 8", "headSha"),
    }
    critical_digests = {
        "Inspect resumable branch, PR, and release state": "b817e8d10d06c935144f6ceb0a0b0c05a8936627d81f1cce3ed2469cab801104",
        "Create or validate exact bump branch": "6ccb0a12b5b5fb20e9ce01ca6341eb16aab78d3af21d2231bbe08d844ce64fe8",
        "Create or validate exact non-draft pull request": "39ebea8bfea27f2503547f93b20bc8118f6a307e374619a11db94b6ed0c23064",
        "Create or resume draft bottle release": "01e0de5aa10a76cf1d8e5e05f3f495b6519ea69c3a75473069ad0ab8050a1408",
        "Read back exact draft assets": "495b4b945d8a8d64864c5cf398ae245e78ddd72c0e73c474f71d7fac58fc667a",
        "Publish exact draft release": "7b97242cab250e1771a43df53b0f72b3e116ff2f255074de05e9909afe13501a",
        "Dispatch or reuse exact-head verification": "44045be2824119bba16a5c9baf9243811560d2f158790cb9e5a10c2a1c04f396",
    }

    def validate(self, workflow):
        self.assertEqual(workflow["on"].keys(), {"workflow_dispatch"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(set(workflow["jobs"]), {"prepare", "build", "state"})
        self.assertNotIn("permissions", workflow["jobs"]["prepare"])
        self.assertNotIn("permissions", workflow["jobs"]["build"])
        self.assertEqual(workflow["jobs"]["state"]["permissions"],
                         {"contents": "write", "pull-requests": "write", "actions": "write"})
        self.assertEqual(workflow["jobs"]["build"]["strategy"]["matrix"]["include"], [
            {"runner": "macos-15", "arch": "arm64", "bottle_tag": "arm64_sequoia"},
            {"runner": "macos-15-intel", "arch": "x86_64", "bottle_tag": "sequoia"}])
        names = [step.get("name") for step in workflow["jobs"]["state"]["steps"]]
        for name, commands in self.critical.items():
            self.assertIn(name, names)
            run = steps(workflow["jobs"]["state"])[name].get("run", "")
            self.assertEqual(hashlib.sha256(run.encode()).hexdigest(), self.critical_digests[name],
                             f"{name}: exact safety-critical run block changed")
            lines = executable(run)
            for command in commands:
                self.assertTrue(any(command in line for line in lines),
                                f"{name}: executable {command} missing")
        order = {name: names.index(name) for name in self.critical}
        self.assertLess(order["Create or validate exact non-draft pull request"],
                        order["Create or resume draft bottle release"])
        self.assertLess(order["Publish exact draft release"],
                        order["Dispatch or reuse exact-head verification"])
        commands = "\n".join(line for job in workflow["jobs"].values()
                             for step in job["steps"] for line in executable(step.get("run", "")))
        self.assertNotIn("immutable-releases", commands)
        self.assertNotIn('release["immutable"]', commands)
        self.assertNotRegex(commands, r"git\s+push[^\n]*(--force(?:-with-lease)?|\s-f(?:\s|$))")
        self.assertNotRegex(commands, r"gh pr merge|--auto|enablePullRequestAutoMerge")
        for use in [s["uses"] for j in workflow["jobs"].values() for s in j["steps"] if "uses" in s]:
            self.assertRegex(use, r"^[^@]+@[0-9a-f]{40}$")

    def test_actual_workflows(self):
        workflow = load_workflow(BUMP_WORKFLOW)
        self.validate(workflow)
        verify = load_workflow(VERIFY_WORKFLOW)
        self.assertEqual(set(verify["jobs"]), {"contract", "bottle"})
        self.assertEqual(verify["permissions"], {"contents": "read"})
        self.assertIn("unittest discover", steps(verify["jobs"]["contract"])["Run automation contracts"]["run"])

    def test_duplicate_yaml_keys_rejected(self):
        with self.assertRaises(ConstructorError):
            yaml.load("x: 1\nx: 2\n", Loader=UniqueBaseLoader)

    def test_commented_or_noop_critical_commands_rejected(self):
        workflow = load_workflow(BUMP_WORKFLOW)
        for name, commands in self.critical.items():
            for command in commands:
                fixture = copy.deepcopy(workflow)
                step = steps(fixture["jobs"]["state"])[name]
                step["run"] = "\n".join("# " + line if command in line else line
                                          for line in step["run"].splitlines())
                with self.subTest(name=name, command=command), self.assertRaises(AssertionError):
                    self.validate(fixture)

    def test_force_push_and_auto_merge_rejected(self):
        for bad in ("git push --force origin HEAD", "gh pr merge --auto"):
            fixture = load_workflow(BUMP_WORKFLOW)
            steps(fixture["jobs"]["state"])["Create or validate exact bump branch"]["run"] += "\n" + bad
            with self.subTest(bad=bad), self.assertRaises(AssertionError):
                self.validate(fixture)

    def test_noop_dispatch_with_required_text_in_comment_rejected(self):
        fixture = load_workflow(BUMP_WORKFLOW)
        step = steps(fixture["jobs"]["state"])["Dispatch or reuse exact-head verification"]
        step["run"] = step["run"].replace(
            "gh workflow run agent-forge-bottles.yml --repo 0k-lab/homebrew-tap --ref \"$BRANCH\"",
            "true # gh workflow run agent-forge-bottles.yml --repo 0k-lab/homebrew-tap --ref \"$BRANCH\"",
        )
        with self.assertRaises(AssertionError):
            self.validate(fixture)

    def test_duplicate_critical_dispatch_rejected(self):
        fixture = load_workflow(BUMP_WORKFLOW)
        step = steps(fixture["jobs"]["state"])["Dispatch or reuse exact-head verification"]
        step["run"] += "\ngh workflow run agent-forge-bottles.yml --repo 0k-lab/homebrew-tap --ref \"$BRANCH\""
        with self.assertRaises(AssertionError):
            self.validate(fixture)

    def test_false_guarded_release_upload_rejected(self):
        fixture = load_workflow(BUMP_WORKFLOW)
        step = steps(fixture["jobs"]["state"])["Create or resume draft bottle release"]
        step["run"] = step["run"].replace(
            'gh release upload "$release_tag" "$path" --repo 0k-lab/homebrew-tap',
            'false && gh release upload "$release_tag" "$path" --repo 0k-lab/homebrew-tap',
        )
        with self.assertRaises(AssertionError):
            self.validate(fixture)

    def test_draft_readback_and_publish_use_release_and_asset_ids(self):
        workflow = load_workflow(BUMP_WORKFLOW)
        state_steps = steps(workflow["jobs"]["state"])
        readback = state_steps["Read back exact draft assets"]["run"]
        self.assertIn("releases?per_page=100", readback)
        self.assertIn('releases/assets/$asset_id', readback)
        self.assertNotIn("releases/tags/", readback)
        self.assertNotIn("gh release download", readback)
        publish = state_steps["Publish exact draft release"]
        self.assertEqual(publish["env"]["RELEASE_ID"], "${{ steps.readback.outputs.release_id }}")
        self.assertIn('repos/0k-lab/homebrew-tap/releases/$RELEASE_ID', publish["run"])
        self.assertNotIn("gh release edit", publish["run"])
        self.assertNotIn("releases/tags/", publish["run"])


if __name__ == "__main__":
    unittest.main()

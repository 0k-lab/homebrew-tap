#!/usr/bin/env python3
"""Fail-closed release, Formula, and bottle handling for Agent Forge bumps."""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath


REPOSITORY = "0k-lab/homebrew-tap"
UPSTREAM = "0k-lab/agent-forge"
BINARIES = ("forge-worker", "forge-codex-plugin", "forge-ref-plugin")
FORMULA_KEY = "0k-lab/tap/agent-forge"
OLD_BOTTLE_TAGS = {"arm64_sonoma", "sequoia"}
NEW_BOTTLE_TAGS = {"arm64_sequoia", "sequoia"}
ARCHITECTURES = {"arm64_sequoia": "arm64", "sequoia": "x86_64"}
TAG_RE = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
ALLOWED_REDIRECT_HOSTS = {"api.github.com", "github.com", "codeload.github.com"}
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def stable_version(tag):
    match = TAG_RE.fullmatch(tag)
    require(match is not None, "version must be exact stable vMAJOR.MINOR.PATCH")
    return tuple(map(int, match.groups()))


def replace_one(text, old, new):
    require(text.count(old) == 1, f"expected exactly one Formula fragment: {old!r}")
    return text.replace(old, new)


def bottle_span(text, required):
    matches = list(re.finditer(r"(?ms)^  bottle do\n.*?^  end\n", text))
    if required:
        require(len(matches) == 1, "Formula must contain exactly one bottle block")
        block = matches[0].group()
        require(len(re.findall(r'^    root_url "[^"]+"$', block, re.MULTILINE)) == 1,
                "bottle block must contain exactly one root_url")
        hashes = re.findall(
            r'^    sha256 cellar: :any_skip_relocation, ([a-z0-9_]+): "([0-9a-f]{64})"$',
            block,
            re.MULTILINE,
        )
        tags = {tag for tag, _ in hashes}
        require(len(hashes) == 2 and tags in (OLD_BOTTLE_TAGS, NEW_BOTTLE_TAGS),
                "bottle block must contain one exact supported tag pair")
        return matches[0].span()
    require(not matches, "source-only Formula must not contain a bottle block")
    return None


def formula_identity(path, require_bottle=True):
    text = Path(path).read_text()
    require(not re.search(r"(?m)^\s*version\s+", text), "explicit Formula version is forbidden")
    require("forge-gate" not in text.lower(), "Formula must remain Worker-only")
    bottle_span(text, require_bottle)

    urls = re.findall(
        r'^  url "https://github\.com/0k-lab/agent-forge/archive/refs/tags/(v[^"/]+)\.tar\.gz"$',
        text,
        re.MULTILINE,
    )
    source_hashes = re.findall(r'^  sha256 "([0-9a-f]{64})"$', text, re.MULTILINE)
    versions = re.findall(r'agent-forge/internal/buildinfo\.Version=(v[^"\s]+)"', text)
    commits = re.findall(r'agent-forge/internal/buildinfo\.Commit=([0-9a-f]+)"', text)
    test_commits = re.findall(r'^    commit = "([0-9a-f]+)"$', text, re.MULTILINE)
    require(len(urls) == len(source_hashes) == len(versions) == len(commits) == len(test_commits) == 1,
            "Formula identity fields must each occur exactly once")
    stable_version(urls[0])
    require(urls[0] == versions[0], "Formula URL and build version differ")
    require(SHA_RE.fullmatch(commits[0]) is not None and commits[0] == test_commits[0],
            "Formula build and test commits must be one lowercase full SHA")
    require(SHA256_RE.fullmatch(source_hashes[0]) is not None, "invalid source SHA-256")

    packages = dict(re.findall(r'^      "([\w-]+)" => "(\./cmd/[\w-]+)",$', text, re.MULTILINE))
    require(packages == {binary: f"./cmd/{binary}" for binary in BINARIES},
            "Formula binary surface changed")
    for binary in BINARIES:
        line = f'assert_equal "{binary} {urls[0]} #{{commit}}", shell_output("#{{bin}}/{binary} --version").strip'
        require(text.count(line) == 1, f"missing exact identity assertion for {binary}")
    for line in ('  depends_on :macos', '  depends_on "go" => :build'):
        require(text.count(line) == 1, f"missing exact dependency: {line.strip()}")

    return {
        "version": urls[0][1:],
        "tag": urls[0],
        "commit": commits[0],
        "source_sha256": source_hashes[0],
    }


class AllowlistedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        require(parsed.scheme == "https" and parsed.hostname in ALLOWED_REDIRECT_HOSTS,
                f"refusing redirect to {newurl}")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        AllowlistedRedirect(),
    )


def request_bytes(url, token=None, maximum=4 * 1024 * 1024,
                  accept="application/vnd.github+json"):
    parsed = urllib.parse.urlparse(url)
    require(parsed.scheme == "https" and parsed.hostname in ALLOWED_REDIRECT_HOSTS,
            f"refusing URL origin {url}")
    headers = {
        "Accept": accept,
        "User-Agent": "homebrew-tap-agent-forge-bump",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with opener().open(request, timeout=30) as response:
        require(response.status == 200, f"unexpected HTTP status {response.status}")
        length = response.headers.get("Content-Length")
        require(length is None or int(length) <= maximum, "response exceeds size limit")
        chunks, size = [], 0
        deadline = time.monotonic() + 120
        while chunk := response.read(min(1024 * 1024, maximum + 1 - size)):
            require(time.monotonic() <= deadline, "response exceeded time limit")
            size += len(chunk)
            require(size <= maximum, "response exceeds size limit")
            chunks.append(chunk)
        return b"".join(chunks)


def request_json(url, token):
    try:
        value = json.loads(request_bytes(url, token).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("GitHub API returned invalid JSON") from error
    require(isinstance(value, dict), "GitHub API response must be an object")
    return value


def safe_members(archive_bytes):
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz")
        members = archive.getmembers()
    except (tarfile.TarError, OSError) as error:
        raise ContractError("invalid gzip tar archive") from error
    require(0 < len(members) <= MAX_ARCHIVE_MEMBERS, "unsafe archive member count")
    total = 0
    roots = set()
    for member in members:
        path = PurePosixPath(member.name)
        require(not path.is_absolute() and ".." not in path.parts and "\\" not in member.name,
                f"unsafe archive path {member.name!r}")
        require(not (member.issym() or member.islnk() or member.isdev()),
                f"unsafe archive member {member.name!r}")
        require(path.parts, "empty archive path")
        roots.add(path.parts[0])
        total += member.size
        require(total <= MAX_EXPANDED_BYTES, "archive expands beyond size limit")
    require(len(roots) == 1, "archive must have exactly one top-level directory")
    return archive, members, roots.pop()


def inspect_source_archive(archive_bytes, version):
    archive, members, root = safe_members(archive_bytes)
    require(root == f"agent-forge-{version}", "unexpected source archive root")
    files = {member.name: member for member in members if member.isfile()}
    go_mod = files.get(f"{root}/go.mod")
    require(go_mod is not None, "source archive lacks go.mod")
    go_mod_file = archive.extractfile(go_mod)
    require(go_mod_file is not None and go_mod_file.read().decode("utf-8").startswith("module agent-forge\n"),
            "source archive has unexpected Go module identity")
    for binary in BINARIES:
        require(f"{root}/cmd/{binary}/main.go" in files, f"source archive lacks {binary}")


def verify_release(args):
    requested = stable_version(args.version)
    current = formula_identity(args.formula)
    require(requested > stable_version(current["tag"]), "target version must be strictly newer than Formula")
    token = os.environ.get("GITHUB_TOKEN")
    require(token, "GITHUB_TOKEN is required")
    api = f"https://api.github.com/repos/{UPSTREAM}"
    tag_path = urllib.parse.quote(args.version, safe="")
    release = request_json(f"{api}/releases/tags/{tag_path}", token)
    commit = release.get("target_commitish")
    require(release.get("tag_name") == args.version, "release tag differs from requested version")
    require(release.get("immutable") is True, "upstream release must be immutable")
    require(release.get("draft") is False and release.get("prerelease") is False,
            "release must be public, non-draft, and non-prerelease")
    require(isinstance(commit, str) and SHA_RE.fullmatch(commit) is not None,
            "target_commitish must be an exact lowercase full SHA")

    ref = request_json(f"{api}/git/ref/tags/{tag_path}", token)
    ref_object = ref.get("object")
    require(isinstance(ref_object, dict) and ref_object.get("type") == "tag"
            and SHA_RE.fullmatch(str(ref_object.get("sha", ""))) is not None,
            "release ref must resolve to an annotated tag object")
    tag_object = request_json(f"{api}/git/tags/{ref_object['sha']}", token)
    target = tag_object.get("object")
    require(tag_object.get("tag") == args.version and isinstance(target, dict)
            and target.get("type") == "commit" and target.get("sha") == commit,
            "annotated tag must point directly to target_commitish")

    source_url = f"https://github.com/{UPSTREAM}/archive/refs/tags/{args.version}.tar.gz"
    archive = request_bytes(source_url, maximum=MAX_ARCHIVE_BYTES, accept="application/octet-stream")
    inspect_source_archive(archive, args.version[1:])
    metadata = {
        "commit": commit,
        "release_id": release.get("id"),
        "source_sha256": hashlib.sha256(archive).hexdigest(),
        "source_url": source_url,
        "tag": args.version,
        "tag_object_sha": ref_object["sha"],
        "version": args.version[1:],
    }
    require(isinstance(metadata["release_id"], int), "release id must be an integer")
    Path(args.output).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    if args.github_output:
        with Path(args.github_output).open("a") as output:
            for key in ("version", "tag", "commit", "source_sha256"):
                output.write(f"{key}={metadata[key]}\n")


def load_metadata(path):
    metadata = json.loads(Path(path).read_text())
    require(isinstance(metadata, dict), "metadata must be an object")
    stable_version(metadata.get("tag"))
    require(metadata.get("version") == metadata["tag"][1:], "metadata version/tag mismatch")
    require(SHA_RE.fullmatch(str(metadata.get("commit", ""))) is not None, "invalid metadata commit")
    require(SHA256_RE.fullmatch(str(metadata.get("source_sha256", ""))) is not None,
            "invalid metadata source SHA-256")
    require(metadata.get("source_url") ==
            f"https://github.com/{UPSTREAM}/archive/refs/tags/{metadata['tag']}.tar.gz",
            "invalid metadata source URL")
    return metadata


def prepare_formula(args):
    metadata = load_metadata(args.metadata)
    current = formula_identity(args.formula)
    require(stable_version(metadata["tag"]) > stable_version(current["tag"]),
            "target version must be strictly newer than Formula")
    text = Path(args.formula).read_text()
    start, end = bottle_span(text, True)
    text = text[:start] + text[end:]
    text = replace_one(text,
        f'https://github.com/{UPSTREAM}/archive/refs/tags/{current["tag"]}.tar.gz',
        metadata["source_url"])
    text = replace_one(text, f'sha256 "{current["source_sha256"]}"',
                       f'sha256 "{metadata["source_sha256"]}"')
    text = replace_one(text,
        f'agent-forge/internal/buildinfo.Version={current["tag"]}',
        f'agent-forge/internal/buildinfo.Version={metadata["tag"]}')
    text = replace_one(text,
        f'agent-forge/internal/buildinfo.Commit={current["commit"]}',
        f'agent-forge/internal/buildinfo.Commit={metadata["commit"]}')
    text = replace_one(text, f'commit = "{current["commit"]}"', f'commit = "{metadata["commit"]}"')
    for binary in BINARIES:
        text = replace_one(text, f'{binary} {current["tag"]} #{{commit}}',
                           f'{binary} {metadata["tag"]} #{{commit}}')
    Path(args.output).write_text(text)
    rendered = formula_identity(args.output, require_bottle=False)
    require(rendered == {key: metadata[key] for key in rendered}, "rendered Formula identity mismatch")


def inspect_bottle_archive(path, tag, commit):
    archive, members, _ = safe_members(Path(path).read_bytes())
    files = {member.name: member for member in members if member.isfile()}
    names = list(files)
    require(not any(PurePosixPath(name).name == "forge-gate" for name in names),
            "bottle contains forbidden forge-gate")
    executables = {PurePosixPath(name).name for name, member in files.items() if member.mode & 0o111}
    require(executables == set(BINARIES), "bottle must contain exactly three executable files")
    selected = {}
    for binary in BINARIES:
        matches = [name for name in names if PurePosixPath(name).parts[-2:] == ("bin", binary)]
        require(len(matches) == 1, f"bottle must contain exactly one bin/{binary}")
        selected[binary] = files[matches[0]]
    with tempfile.TemporaryDirectory(prefix="agent-forge-bottle-") as directory:
        directory = Path(directory)
        for binary, member in selected.items():
            source = archive.extractfile(member)
            require(source is not None, f"cannot read {binary}")
            target = directory / binary
            target.write_bytes(source.read())
            target.chmod(0o700)
            result = subprocess.run([target, "--version"], capture_output=True, text=True,
                                    timeout=15, check=False)
            require(result.returncode == 0 and result.stderr == ""
                    and result.stdout.strip() == f"{binary} {tag} {commit}",
                    f"bottle {binary} identity mismatch")


def bottle_entry(data, version, tag, root_url):
    require(isinstance(data, dict) and set(data) == {FORMULA_KEY},
            "unexpected bottle JSON Formula")
    entry = data[FORMULA_KEY]
    formula = entry.get("formula", {})
    expected_formula = {
        "name": "agent-forge",
        "pkg_version": version,
        "path": "Library/Taps/0k-lab/homebrew-tap/Formula/agent-forge.rb",
        "tap_git_path": "Formula/agent-forge.rb",
        "tap_git_remote": "https://github.com/0k-lab/homebrew-tap",
        "homepage": "https://github.com/0k-lab/agent-forge",
    }
    for key, value in expected_formula.items():
        require(formula.get(key) == value, f"bottle Formula {key} mismatch")
    require(SHA_RE.fullmatch(str(formula.get("tap_git_revision", ""))) is not None,
            "bottle Formula tap revision must be a full SHA")
    bottle = entry.get("bottle", {})
    require(bottle.get("root_url") == root_url, "bottle root URL mismatch")
    require(bottle.get("cellar") == "any_skip_relocation",
            "bottle cellar must be any_skip_relocation")
    require(bottle.get("rebuild") == 0, "bottle rebuild must be zero")
    tags = bottle.get("tags")
    require(isinstance(tags, dict) and set(tags) == {tag}, "unexpected or duplicate bottle tag")
    tag_data = tags[tag]
    require(tag in ARCHITECTURES and tag_data.get("tab", {}).get("arch") == ARCHITECTURES[tag],
            "bottle architecture mismatch")
    require(tag_data.get("path_exec_files") == [
        "bin/forge-codex-plugin", "bin/forge-worker", "bin/forge-ref-plugin"
    ], "bottle executable paths mismatch")
    all_files = tag_data.get("all_files")
    require(isinstance(all_files, list) and all(isinstance(name, str) for name in all_files),
            "invalid bottle all_files")
    require({name for name in all_files if name.startswith("bin/")} == {
        "bin/forge-codex-plugin", "bin/forge-worker", "bin/forge-ref-plugin"
    }, "bottle all_files executable paths mismatch")
    require(not any(PurePosixPath(name).name == "forge-gate" for name in all_files),
            "bottle metadata contains forbidden forge-gate")
    return entry, bottle, tag_data


def validate_bottle(args):
    directory = Path(args.directory)
    files = [path for path in directory.iterdir() if path.is_file()]
    json_files = [path for path in files if path.name.endswith(".bottle.json")]
    require(len(json_files) == 1, "brew bottle must produce exactly one JSON file")
    data = json.loads(json_files[0].read_text())
    _, _, tag_data = bottle_entry(data, args.version, args.tag, args.root_url)
    digest = tag_data.get("sha256")
    local_name = tag_data.get("local_filename")
    remote_name = tag_data.get("filename")
    require(SHA256_RE.fullmatch(str(digest)) is not None, "invalid bottle SHA-256")
    require(isinstance(local_name, str) and PurePosixPath(local_name).name == local_name,
            "unsafe bottle local filename")
    require(remote_name == f"agent-forge-{args.version}.{args.tag}.bottle.tar.gz",
            "bottle JSON contains a non-canonical public filename")
    tar_path = directory / local_name
    require(tar_path.is_file(), "JSON-derived bottle tar is missing")
    tar_files = [path for path in files if path.name.endswith(".bottle.tar.gz")]
    require(tar_files == [tar_path], "brew bottle must produce exactly one tar file")
    require(hashlib.sha256(tar_path.read_bytes()).hexdigest() == digest, "bottle tar digest mismatch")
    inspect_bottle_archive(tar_path, f"v{args.version}", args.commit)

    canonical = remote_name
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(tar_path, output / canonical)
    tag_data["local_filename"] = canonical
    (output / f"agent-forge-{args.version}.{args.tag}.bottle.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n")


def render_final(args):
    source = formula_identity(args.formula, require_bottle=False)
    directory = Path(args.artifacts)
    files = [path for path in directory.rglob("*") if path.is_file()]
    require(len(files) == 4, "expected exactly two bottle tar files and two JSON files")
    json_files = [path for path in files if path.name.endswith(".bottle.json")]
    tar_files = [path for path in files if path.name.endswith(".bottle.tar.gz")]
    require(len(json_files) == len(tar_files) == 2, "missing or extra bottle artifacts")
    bottles = {}
    for path in json_files:
        data = json.loads(path.read_text())
        require(isinstance(data, dict) and set(data) == {FORMULA_KEY},
                "bottle metadata must be full Homebrew JSON")
        tags = data[FORMULA_KEY].get("bottle", {}).get("tags", {})
        require(isinstance(tags, dict) and len(tags) == 1, "bottle JSON must contain one tag")
        tag = next(iter(tags))
        require(tag in NEW_BOTTLE_TAGS and tag not in bottles,
                "missing, unexpected, or duplicate bottle tag")
        root_url = f"https://github.com/{REPOSITORY}/releases/download/agent-forge-{source['tag']}"
        _, bottle, tag_data = bottle_entry(data, source["version"], tag, root_url)
        digest = tag_data.get("sha256")
        filename = tag_data.get("filename")
        require(SHA256_RE.fullmatch(str(digest)) is not None, "invalid bottle metadata SHA-256")
        require(filename == f"agent-forge-{source['version']}.{tag}.bottle.tar.gz",
                "non-canonical bottle filename")
        matching = [tar for tar in tar_files if tar.name == filename]
        require(len(matching) == 1, "metadata must identify exactly one bottle tar")
        require(hashlib.sha256(matching[0].read_bytes()).hexdigest() == digest,
                "published bottle tar digest mismatch")
        bottles[tag] = {"root_url": bottle["root_url"], "sha256": digest}
    require(set(bottles) == NEW_BOTTLE_TAGS, "both supported bottle tags are required")

    block = (
        "  bottle do\n"
        f'    root_url "{bottles["arm64_sequoia"]["root_url"]}"\n'
        f'    sha256 cellar: :any_skip_relocation, arm64_sequoia: "{bottles["arm64_sequoia"]["sha256"]}"\n'
        f'    sha256 cellar: :any_skip_relocation, sequoia: "{bottles["sequoia"]["sha256"]}"\n'
        "  end\n\n"
    )
    text = Path(args.formula).read_text()
    marker = f'  sha256 "{source["source_sha256"]}"\n\n'
    text = replace_one(text, marker, marker + block)
    Path(args.output).write_text(text)
    require(formula_identity(args.output)["tag"] == source["tag"], "final Formula identity mismatch")
    Path(args.metadata).write_text(json.dumps({
        "arm64_sequoia": bottles["arm64_sequoia"]["sha256"],
        "sequoia": bottles["sequoia"]["sha256"],
    }, indent=2, sort_keys=True) + "\n")


def identity_command(args):
    identity = formula_identity(args.formula)
    if args.github_output:
        with Path(args.github_output).open("a") as output:
            for key, value in identity.items():
                output.write(f"{key}={value}\n")
    else:
        print(json.dumps(identity, sort_keys=True))


def plan_state(expected, state):
    require(isinstance(expected, dict) and isinstance(state, dict), "state inputs must be objects")
    branch = state.get("branch")
    plan = {"branch_action": "create" if branch is None else "reuse"}
    if branch is not None:
        require(branch == {
            "sha": expected["branch_sha"],
            "parent_sha": expected["base_sha"],
            "files": ["Formula/agent-forge.rb"],
            "formula_sha256": expected["formula_sha256"],
        }, "existing branch conflicts with exact rendered state")

    prs = state.get("prs")
    require(isinstance(prs, list) and len(prs) <= 1, "conflicting pull request state")
    if prs:
        require(branch is not None, "pull request exists without exact branch")
        pr = prs[0]
        require(pr.get("state") == "OPEN" and pr.get("isDraft") is False
                and pr.get("baseRefName") == "main"
                and pr.get("headRefName") == expected["branch"]
                and pr.get("headRefOid") == expected["branch_sha"]
                and pr.get("title") == expected["title"]
                and pr.get("body") == expected["body"],
                "existing pull request conflicts with exact expected state")
        plan.update(pr_action="reuse", pr_number=pr.get("number"), pr_url=pr.get("url"))
    else:
        plan.update(pr_action="create", pr_number=None, pr_url=None)

    release = state.get("release")
    if release is None:
        require(state.get("release_tag_sha") is None, "release tag exists without exact release")
        plan.update(release_action="create_draft", release_id=None,
                    missing_assets=sorted(expected["assets"]))
        return plan
    identity = expected["release"]
    require(all(release.get(key) == value for key, value in identity.items())
            and release.get("prerelease") is False and isinstance(release.get("id"), int),
            "existing release conflicts with exact expected identity")
    actual_assets = release.get("assets")
    require(isinstance(actual_assets, list), "release assets must be a list")
    actual = {}
    for asset in actual_assets:
        name = asset.get("name")
        require(name not in actual and name in expected["assets"], "unexpected or duplicate release asset")
        wanted = expected["assets"][name]
        require(asset.get("size") == wanted["size"] and asset.get("digest") == wanted["digest"],
                f"conflicting release asset {name}")
        actual[name] = asset
    missing = sorted(set(expected["assets"]) - set(actual))
    if release.get("draft") is True:
        require(state.get("release_tag_sha") in (None, expected["base_sha"]),
                "draft release tag conflicts with expected target")
        plan.update(release_action="publish" if not missing else "resume_draft",
                    release_id=release["id"], missing_assets=missing)
    else:
        require(release.get("draft") is False and not missing
                and state.get("release_tag_sha") == expected["base_sha"],
                "public release is incomplete or has invalid draft state")
        plan.update(release_action="complete", release_id=release["id"], missing_assets=[])
    plan["asset_ids"] = {name: asset.get("id") for name, asset in actual.items()}
    return plan


def find_dispatch_run(runs, branch, commit):
    require(isinstance(runs, list), "workflow runs must be a list")
    matches = [run for run in runs if run.get("event") == "workflow_dispatch"
               and run.get("headBranch") == branch and run.get("headSha") == commit]
    require(all(isinstance(run.get("databaseId"), int) and isinstance(run.get("url"), str)
                for run in matches), "invalid matching workflow run")
    return max(matches, key=lambda run: run["databaseId"]) if matches else None


def state_plan_command(args):
    plan = plan_state(json.loads(Path(args.expected).read_text()),
                      json.loads(Path(args.state).read_text()))
    Path(args.output).write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    if args.github_output:
        with Path(args.github_output).open("a") as output:
            for key in ("branch_action", "pr_action", "pr_number", "pr_url",
                        "release_action", "release_id"):
                value = plan.get(key)
                output.write(f"{key}={'' if value is None else value}\n")
            output.write(f"missing_assets={json.dumps(plan.get('missing_assets', []), separators=(',', ':'))}\n")


def parser():
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify-release")
    verify.add_argument("--version", required=True)
    verify.add_argument("--formula", required=True)
    verify.add_argument("--output", required=True)
    verify.add_argument("--github-output")
    verify.set_defaults(run=verify_release)

    prepare = commands.add_parser("prepare-formula")
    prepare.add_argument("--formula", required=True)
    prepare.add_argument("--metadata", required=True)
    prepare.add_argument("--output", required=True)
    prepare.set_defaults(run=prepare_formula)

    validate = commands.add_parser("validate-bottle")
    validate.add_argument("--directory", required=True)
    validate.add_argument("--version", required=True)
    validate.add_argument("--tag", required=True, choices=tuple(sorted(NEW_BOTTLE_TAGS)))
    validate.add_argument("--root-url", required=True)
    validate.add_argument("--commit", required=True)
    validate.add_argument("--output", required=True)
    validate.set_defaults(run=validate_bottle)

    render = commands.add_parser("render-final")
    render.add_argument("--formula", required=True)
    render.add_argument("--artifacts", required=True)
    render.add_argument("--output", required=True)
    render.add_argument("--metadata", required=True)
    render.set_defaults(run=render_final)

    identity = commands.add_parser("formula-identity")
    identity.add_argument("--formula", required=True)
    identity.add_argument("--github-output")
    identity.set_defaults(run=identity_command)

    state = commands.add_parser("state-plan")
    state.add_argument("--expected", required=True)
    state.add_argument("--state", required=True)
    state.add_argument("--output", required=True)
    state.add_argument("--github-output")
    state.set_defaults(run=state_plan_command)
    return root


def main():
    try:
        args = parser().parse_args()
        args.run(args)
    except (ContractError, OSError, KeyError, TypeError, json.JSONDecodeError,
            urllib.error.URLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

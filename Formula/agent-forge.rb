class AgentForge < Formula
  desc "Worker runtime and plugins for Agent Forge"
  homepage "https://github.com/0k-lab/agent-forge"
  url "https://github.com/0k-lab/agent-forge/archive/refs/tags/v0.1.7.tar.gz"
  sha256 "bec4fb1a01935d2df21166aca4d6ef7207b35fa1ecade07ca53998918f941596"

  bottle do
    root_url "https://github.com/0k-lab/homebrew-tap/releases/download/agent-forge-v0.1.7-1"
    sha256 cellar: :any_skip_relocation, arm64_sonoma: "ccb029de9cd5c57171dab3b3c8e8ac4d1b4128264a132f963139b0cd311926a8"
    sha256 cellar: :any_skip_relocation, sequoia: "32f3e8460d4e02af444b77ef5e4354d233a1144e3fa74cf89e387b72d824558d"
  end

  depends_on :macos
  depends_on "go" => :build

  def install
    ENV["CGO_ENABLED"] = "0"
    ENV["GOFLAGS"] = "-mod=readonly"
    ENV["GOENV"] = "off"
    ENV["GOWORK"] = "off"
    ENV["GOEXPERIMENT"] = ""
    ENV["GOFIPS140"] = "off"

    ldflags = [
      "-s", "-w", "-buildid=",
      "-X", "agent-forge/internal/buildinfo.Version=v0.1.7",
      "-X", "agent-forge/internal/buildinfo.Commit=ee034b53b5af660cfca66985959b566ecfdd0f73",
    ].join(" ")
    {
      "forge-worker" => "./cmd/forge-worker",
      "forge-codex-plugin" => "./cmd/forge-codex-plugin",
      "forge-ref-plugin" => "./cmd/forge-ref-plugin",
    }.each do |name, package|
      system "go", "build", "-trimpath", "-buildvcs=false", "-ldflags", ldflags, "-o", bin/name, package
    end
  end

  test do
    commit = "ee034b53b5af660cfca66985959b566ecfdd0f73"
    assert_equal "forge-worker v0.1.7 #{commit}", shell_output("#{bin}/forge-worker --version").strip
    assert_equal "forge-codex-plugin v0.1.7 #{commit}", shell_output("#{bin}/forge-codex-plugin --version").strip
    assert_equal "forge-ref-plugin v0.1.7 #{commit}", shell_output("#{bin}/forge-ref-plugin --version").strip
  end
end

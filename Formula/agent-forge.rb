class AgentForge < Formula
  desc "Worker runtime and plugins for Agent Forge"
  homepage "https://github.com/0k-lab/agent-forge"
  url "https://github.com/0k-lab/agent-forge/archive/refs/tags/v0.1.8.tar.gz"
  sha256 "952fd96e058f48d5464d8528202024d77d20948c0c82b43c835b63061bf56032"

  bottle do
    root_url "https://github.com/0k-lab/homebrew-tap/releases/download/agent-forge-v0.1.8"
    sha256 cellar: :any_skip_relocation, arm64_sequoia: "f3e4b62e4ea9f6b1d2403b49b199f717bcec6592efb9bc6e7ae48c5866e45555"
    sha256 cellar: :any_skip_relocation, sequoia: "b41ba25f4de54920a1ffe0bdbfde8b0349cbe96142a3a35d3df30601f164d2e1"
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
      "-X", "agent-forge/internal/buildinfo.Version=v0.1.8",
      "-X", "agent-forge/internal/buildinfo.Commit=2c03fd4c797ead5dd10cd786e9f7a0ea8a702e8f",
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
    commit = "2c03fd4c797ead5dd10cd786e9f7a0ea8a702e8f"
    assert_equal "forge-worker v0.1.8 #{commit}", shell_output("#{bin}/forge-worker --version").strip
    assert_equal "forge-codex-plugin v0.1.8 #{commit}", shell_output("#{bin}/forge-codex-plugin --version").strip
    assert_equal "forge-ref-plugin v0.1.8 #{commit}", shell_output("#{bin}/forge-ref-plugin --version").strip
  end
end

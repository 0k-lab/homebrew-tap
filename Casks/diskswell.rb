cask "diskswell" do
  version "1.1.0"
  sha256 "d6db97ed1a86c1888de3508d94e0afd748514c5d5ee1c2f5ff760e906086be9a"

  url "https://github.com/kricha-lab/DiskSwell/releases/download/v#{version}/DiskSwell.pkg"
  name "DiskSwell"
  desc "Detect abnormal disk-usage growth"
  homepage "https://github.com/kricha-lab/DiskSwell"

  depends_on macos: ">= :sonoma"

  pkg "DiskSwell.pkg"

  uninstall quit: "com.diskswell.DiskSwell",
            pkgutil: "com.diskswell.DiskSwell.pkg"
end

cask "diskswell" do
  version "1.2.2"
  sha256 "e6364930a40264c5513366c84d4d1dd3d0d281595bc679b19861a059a0c4e06d"

  url "https://github.com/0k-lab/DiskSwell/releases/download/v#{version}/DiskSwell.pkg"
  name "DiskSwell"
  desc "Detect abnormal disk-usage growth"
  homepage "https://github.com/0k-lab/DiskSwell"

  depends_on macos: ">= :sonoma"

  pkg "DiskSwell.pkg"

  uninstall quit: "com.diskswell.DiskSwell",
            pkgutil: "com.diskswell.DiskSwell.pkg"
end

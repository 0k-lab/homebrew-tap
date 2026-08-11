cask "diskswell" do
  version "1.2.1"
  sha256 "73ba314111450728d13c0e943e234fa5f76d64b5d5630dd96c3a1db790d444fb"

  url "https://github.com/0k-lab/DiskSwell/releases/download/v#{version}/DiskSwell.pkg"
  name "DiskSwell"
  desc "Detect abnormal disk-usage growth"
  homepage "https://github.com/0k-lab/DiskSwell"

  depends_on macos: ">= :sonoma"

  pkg "DiskSwell.pkg"

  uninstall quit: "com.diskswell.DiskSwell",
            pkgutil: "com.diskswell.DiskSwell.pkg"
end

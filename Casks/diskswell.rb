cask "diskswell" do
  version "1.1.1"
  sha256 "43c0911d4e40134b7ebae7c52e52f182d53af6a09a38cbcfa4a5646b475cd83c"

  url "https://github.com/kricha-lab/DiskSwell/releases/download/v#{version}/DiskSwell.pkg"
  name "DiskSwell"
  desc "Detect abnormal disk-usage growth"
  homepage "https://github.com/kricha-lab/DiskSwell"

  depends_on macos: ">= :sonoma"

  pkg "DiskSwell.pkg"

  uninstall quit: "com.diskswell.DiskSwell",
            pkgutil: "com.diskswell.DiskSwell.pkg"
end

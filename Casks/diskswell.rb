cask "diskswell" do
  version "1.0.0"
  sha256 "c41fcd1e5e7d0e9db60f39cbc98f4af4addb3cb438fe159ef18fbcc3d0ee5759"

  url "https://github.com/kricha-lab/DiskSwell/releases/download/v#{version}/DiskSwell.pkg"
  name "DiskSwell"
  desc "Detect abnormal disk-usage growth"
  homepage "https://github.com/kricha-lab/DiskSwell"

  depends_on macos: ">= :sonoma"

  pkg "DiskSwell.pkg"

  uninstall quit: "com.diskswell.DiskSwell",
            pkgutil: "com.diskswell.DiskSwell.pkg"
end

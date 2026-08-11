cask "diskswell" do
  version "1.2.0"
  sha256 "437fe9fbf7deb75f17da8256919368335aa162f9ac149886558fb4bd515bda24"

  url "https://github.com/0k-lab/DiskSwell/releases/download/v#{version}/DiskSwell.pkg"
  name "DiskSwell"
  desc "Detect abnormal disk-usage growth"
  homepage "https://github.com/0k-lab/DiskSwell"

  depends_on macos: ">= :sonoma"

  pkg "DiskSwell.pkg"

  uninstall quit: "com.diskswell.DiskSwell",
            pkgutil: "com.diskswell.DiskSwell.pkg"
end

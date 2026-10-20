// swift-tools-version:6.0
// What the iPad keyboard does that needs no UIKit: the virtual firmware in JavaScriptCore (JSKbd,
// over hud/kbd.js), what it types done to a text field (TextSink), and which key a finger is on
// (HitTest). Tested on the Mac with `swift test`; the keyboard extension and the app use it as is.
import PackageDescription

let package = Package(
    name: "ZmkHudCore",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "ZmkHudCore", targets: ["ZmkHudCore"])],
    targets: [
        .target(name: "ZmkHudCore"),
        .testTarget(name: "ZmkHudCoreTests", dependencies: ["ZmkHudCore"]),
    ]
)

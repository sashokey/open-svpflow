// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "OpenSVPflow",
    platforms: [.macOS(.v26)],
    products: [.executable(name: "OpenSVPflow", targets: ["OpenSVPflow"])],
    targets: [
        .target(name: "OpenSVPflowCore"),
        .executableTarget(name: "OpenSVPflow", dependencies: ["OpenSVPflowCore"]),
        .testTarget(name: "OpenSVPflowCoreTests", dependencies: ["OpenSVPflowCore"]),
        .testTarget(name: "OpenSVPflowUITests", dependencies: ["OpenSVPflow", "OpenSVPflowCore"])
    ]
)

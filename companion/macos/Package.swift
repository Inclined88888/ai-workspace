// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RooksCompanion",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "RooksCompanion", targets: ["RooksCompanion"]),
        .library(name: "CompanionCore", targets: ["CompanionCore"]),
    ],
    targets: [
        .target(
            name: "CompanionCore",
            dependencies: [],
            path: "Sources/CompanionCore"
        ),
        .executableTarget(
            name: "RooksCompanion",
            dependencies: ["CompanionCore"],
            path: "Sources/RooksCompanion"
        ),
        .testTarget(
            name: "CompanionCoreTests",
            dependencies: ["CompanionCore"],
            path: "Tests/CompanionCoreTests"
        ),
    ]
)
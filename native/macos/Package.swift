// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ORFGateway",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "ORFGatewayCore", targets: ["ORFGatewayCore"]),
        .executable(name: "orf-chrome-host", targets: ["ChromeHost"]),
        .executable(name: "orf-gateway-app", targets: ["GatewayApp"])
    ],
    targets: [
        .target(name: "ORFGatewayCore", path: "Shared", swiftSettings: [.define("CHROME_GATEWAY")]),
        .executableTarget(name: "ChromeHost", dependencies: ["ORFGatewayCore"], path: "ChromeHost"),
        .executableTarget(name: "GatewayApp", dependencies: ["ORFGatewayCore"], path: "App",
            exclude: ["Info.plist", "Gateway.entitlements"]),
        .testTarget(name: "ORFGatewayCoreTests", dependencies: ["ORFGatewayCore"], path: "Tests",
                    exclude: ["browser-smoke.mjs", "chrome-smoke.mjs"])
    ]
)

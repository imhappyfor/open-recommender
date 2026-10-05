import CryptoKit
import Foundation
import ORFGatewayCore

func writeResponse(_ response: [String: Any]) throws {
    let data = try JSONSerialization.data(withJSONObject: response)
    var length = UInt32(data.count).littleEndian
    try withUnsafeBytes(of: &length) { try FileHandle.standardOutput.write(contentsOf: Data($0)) }
    try FileHandle.standardOutput.write(contentsOf: data)
}

func readExactly(_ count: Int) throws -> Data {
    var data = Data()
    while data.count < count {
        guard let chunk = try FileHandle.standardInput.read(upToCount: count - data.count), !chunk.isEmpty else {
            throw GatewayError.invalid("Incomplete native message.")
        }
        data.append(chunk)
    }
    return data
}

func registerHost(directory: URL) throws {
    let binary = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL.resolvingSymlinksInPath()
    guard FileManager.default.isExecutableFile(atPath: binary.path) else { throw GatewayError.invalid("Host is not executable.") }
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let path = directory.appendingPathComponent("org.openrecommender.gateway.json")
    let manifest: [String: Any] = ["name": "org.openrecommender.gateway", "description": "ORF Gateway native bridge",
        "path": binary.path, "type": "stdio", "allowed_origins": ["chrome-extension://\(Gateway.chromeExtensionID)/"]]
    let data = try JSONSerialization.data(withJSONObject: manifest, options: [.sortedKeys, .prettyPrinted])
    if FileManager.default.fileExists(atPath: path.path) {
        guard try path.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink != true,
              try Data(contentsOf: path) == data else {
            throw GatewayError.invalid("An existing host registration differs. Preserve/remove it yourself before registering this build.")
        }
    } else { try data.write(to: path, options: .withoutOverwriting) }
    print(path.path)
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    if args == ["--register"] || (args.count == 3 && args[0] == "--register" && args[1] == "--directory") {
        let directory = args.count == 3 ? URL(fileURLWithPath: args[2]) : FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Google/Chrome/NativeMessagingHosts")
        try registerHost(directory: directory)
    } else {
        #if DEBUG
        if args.count == 2 && ["--test-approve", "--test-deny"].contains(args[0]),
           ProcessInfo.processInfo.environment["ORF_GATEWAY_TEST_DIRECTORY"] != nil {
            // Synthetic-test driver only; never exposed as a browser operation and never accesses Keychain.
            let mailbox = try RequestMailbox(directory: Gateway.sharedDirectory(), allowLoopback: true)
            if args[0] == "--test-deny" { try mailbox.deny(id: args[1]) }
            else { try mailbox.approve(id: args[1], key: Curve25519.Signing.PrivateKey(rawRepresentation: Data(0..<32))) }
            exit(0)
        }
        #endif
        guard args == ["chrome-extension://\(Gateway.chromeExtensionID)/"] else {
            throw GatewayError.invalid("Only the registered ORF Chrome extension may call this host.")
        }
        let header = try readExactly(4)
        let length = header.withUnsafeBytes { UInt32(littleEndian: $0.loadUnaligned(as: UInt32.self)) }
        guard length > 0 && length <= 2048 else { throw GatewayError.invalid("Invalid native frame size.") }
        guard let message = try JSONSerialization.jsonObject(with: readExactly(Int(length))) as? [String: Any] else {
            throw GatewayError.invalid("Native messages must be JSON objects.")
        }
        let mailbox = try RequestMailbox(directory: Gateway.sharedDirectory(), allowLoopback: Gateway.allowLoopback)
        try writeResponse(Gateway.handle(message, mailbox: mailbox))
    }
} catch {
    if CommandLine.arguments.dropFirst().first == "--register" {
        try? FileHandle.standardError.write(contentsOf: Data((error.localizedDescription + "\n").utf8))
        exit(1)
    }
    try? writeResponse(Gateway.publicError)
    exit(1)
}

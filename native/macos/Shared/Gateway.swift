import CryptoKit
import Darwin
import Foundation

public enum GatewayError: Error, LocalizedError {
    case invalid(String)
    public var errorDescription: String? {
        switch self { case .invalid(let message): return message }
    }
}

public enum RequestStatus: String, Codable, Sendable {
    case pending, approved, denied, revoked, expired
}

public struct LoginPayload: Codable, Equatable, Sendable {
    public let version: String
    public let audience: String
    public let nonce: String
    public let subject: String
    public let public_key: String
    public let issued_at: Int
    public let expires_at: Int
}

public struct LoginProof: Codable, Equatable, Sendable {
    public let payload: LoginPayload
    public let signature: String
}

public struct ConnectionRequest: Codable, Identifiable, Sendable {
    public var id: String { request_id }
    public let request_id: String
    public let origin: String
    public let nonce: String
    public let expires_at: Int
    public var status: RequestStatus = .pending
    public var proof: LoginProof?
}

public enum Gateway {
    public static let version = "orf-native-connect-v1"
    public static let signaturePrefix = Data("ORF native connect v1\n".utf8)
    public static let chromeExtensionID = "lialdifcbcjmjahaicjmkmklmfjoelom"
    public static let publicError = ["status": "error", "error": "ORF rejected the request or is not configured. Open ORF Gateway to review setup."]

    public static var allowLoopback: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    public static func base64URL(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }

    public static func decodeBase64URL(_ value: String, count: Int) throws -> Data {
        guard value.utf8.allSatisfy({ (65...90).contains($0) || (97...122).contains($0)
            || (48...57).contains($0) || $0 == 45 || $0 == 95 }),
              let data = Data(base64Encoded: value.replacingOccurrences(of: "-", with: "+")
                .replacingOccurrences(of: "_", with: "/") + String(repeating: "=", count: (4 - value.count % 4) % 4)),
              data.count == count, base64URL(data) == value else {
            throw GatewayError.invalid("Invalid base64url value.")
        }
        return data
    }

    public static func validateOrigin(_ origin: String, allowLoopback: Bool = false) throws {
        guard origin.utf8.count <= 300, origin.utf8.allSatisfy({ $0 > 32 && $0 < 127 }),
              let parts = URLComponents(string: origin), let scheme = parts.scheme,
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.path.isEmpty, parts.query == nil, parts.fragment == nil else {
            throw GatewayError.invalid("A canonical website origin is required.")
        }
        let loopback = ["localhost", "127.0.0.1", "[::1]"].contains(host)
        guard scheme == "https" || (allowLoopback && loopback && scheme == "http"),
              host == host.lowercased(), !host.contains("%"),
              parts.port == nil || (1...65535).contains(parts.port!),
              !(scheme == "https" && parts.port == 443), !(scheme == "http" && parts.port == 80),
              origin == "\(scheme)://\(host)" + (parts.port.map { ":\($0)" } ?? "") else {
            throw GatewayError.invalid("Only canonical HTTPS origins are accepted (loopback HTTP in Debug).")
        }
        if host != "[::1]" {
            guard host.count <= 253, host.split(separator: ".", omittingEmptySubsequences: false).allSatisfy({ label in
                !label.isEmpty && label.count <= 63 && label.first != "-" && label.last != "-"
                    && label.utf8.allSatisfy({ (97...122).contains($0) || (48...57).contains($0) || $0 == 45 })
            }) else { throw GatewayError.invalid("Invalid website hostname.") }
        }
    }

    public static func canonicalPayload(_ payload: LoginPayload) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(payload)
    }

    public static func makeProof(request: ConnectionRequest, key: Curve25519.Signing.PrivateKey,
                                 now: Int) throws -> LoginProof {
        guard request.status == .pending, now < request.expires_at else {
            throw GatewayError.invalid("This request is no longer awaiting approval.")
        }
        let publicKey = key.publicKey.rawRepresentation
        let digest = SHA256.hash(data: publicKey).map { String(format: "%02x", $0) }.joined()
        let payload = LoginPayload(version: version, audience: request.origin, nonce: request.nonce,
            subject: "orf:site:" + digest.prefix(32), public_key: base64URL(publicKey),
            issued_at: now, expires_at: request.expires_at)
        return try LoginProof(payload: payload,
            signature: base64URL(key.signature(for: signaturePrefix + canonicalPayload(payload))))
    }

    public static func sharedDirectory() throws -> URL {
        #if CHROME_GATEWAY
        #if DEBUG
        if let path = ProcessInfo.processInfo.environment["ORF_GATEWAY_TEST_DIRECTORY"] {
            let directory = URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath()
            let temporary = FileManager.default.temporaryDirectory.standardizedFileURL.resolvingSymlinksInPath()
            guard directory.path.hasPrefix(temporary.path + "/") || directory.path.hasPrefix("/private/tmp/") else {
                throw GatewayError.invalid("Test mailboxes must be inside a temporary directory.")
            }
            return directory
        }
        #endif
        return try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
            appropriateFor: nil, create: true).appendingPathComponent("org.openrecommender.gateway.chrome/GatewayRequests")
        #else
        guard let group = Bundle.main.object(forInfoDictionaryKey: "ORFAppGroup") as? String,
              !group.hasPrefix("."), let directory = FileManager.default.containerURL(
                forSecurityApplicationGroupIdentifier: group) else {
            throw GatewayError.invalid("Configure the same signing team and App Group for both Xcode targets.")
        }
        return directory.appendingPathComponent("GatewayRequests", isDirectory: true)
        #endif
    }

    public static func handle(_ message: [String: Any], mailbox: RequestMailbox) throws -> [String: Any] {
        guard JSONSerialization.isValidJSONObject(message),
              try JSONSerialization.data(withJSONObject: message).count <= 2048,
              let operation = message["operation"] as? String,
              let id = message["request_id"] as? String,
              let origin = message["origin"] as? String else {
            throw GatewayError.invalid("Invalid native message.")
        }
        let request: ConnectionRequest
        switch operation {
        case "begin":
            guard Set(message.keys) == ["operation", "request_id", "origin", "nonce", "expires_at"],
                  let nonce = message["nonce"] as? String, let number = message["expires_at"] as? NSNumber,
                  CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue == Double(number.intValue) else {
                throw GatewayError.invalid("Invalid connection request fields.")
            }
            request = try mailbox.begin(id: id, origin: origin, nonce: nonce, expiresAt: number.intValue)
        case "status":
            guard Set(message.keys) == ["operation", "request_id", "origin"] else {
                throw GatewayError.invalid("Invalid status request fields.")
            }
            request = try mailbox.status(id: id, origin: origin)
        default: throw GatewayError.invalid("Unsupported native operation.")
        }
        var result: [String: Any] = ["status": request.status.rawValue]
        if let proof = request.proof {
            result["proof"] = try JSONSerialization.jsonObject(with: JSONEncoder().encode(proof))
        }
        return result
    }
}

// Only requests and approved public proofs enter this local mailbox. Keys stay in the app's Keychain.
public final class RequestMailbox {
    private let directory: URL
    private let allowLoopback: Bool
    public init(directory: URL, allowLoopback: Bool = false) throws {
        self.directory = directory
        self.allowLoopback = allowLoopback
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
    }

    private func locked<T>(_ body: () throws -> T) throws -> T {
        let descriptor = open(directory.appendingPathComponent("mailbox.lock").path,
            O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw GatewayError.invalid("Cannot lock the request mailbox.") }
        defer { close(descriptor) }
        guard flock(descriptor, LOCK_EX) == 0 else { throw GatewayError.invalid("Cannot lock the request mailbox.") }
        defer { flock(descriptor, LOCK_UN) }
        return try body()
    }

    private func path(_ id: String) throws -> URL {
        guard UUID(uuidString: id)?.uuidString.lowercased() == id else {
            throw GatewayError.invalid("Invalid request identifier.")
        }
        return directory.appendingPathComponent(id + ".json")
    }

    private func read(_ id: String) throws -> ConnectionRequest {
        let url = try path(id)
        guard try url.resourceValues(forKeys: [.isSymbolicLinkKey, .fileSizeKey]).isSymbolicLink != true,
              (try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? Int.max) < 8192 else {
            throw GatewayError.invalid("Invalid request file.")
        }
        let request = try JSONDecoder().decode(ConnectionRequest.self, from: Data(contentsOf: url))
        guard request.request_id == id else { throw GatewayError.invalid("Invalid request file identity.") }
        return request
    }

    private func save(_ request: ConnectionRequest) throws {
        let url = try path(request.id)
        if FileManager.default.fileExists(atPath: url.path),
           try url.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink == true {
            throw GatewayError.invalid("Refusing a linked request file.")
        }
        try JSONEncoder().encode(request).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    private func records(now: Int) throws -> [ConnectionRequest] {
        var requests: [ConnectionRequest] = []
        for url in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            where url.pathExtension == "json" {
            let request = try read(url.deletingPathExtension().lastPathComponent)
            if request.expires_at + 300 <= now { try FileManager.default.removeItem(at: url) }
            else { requests.append(request) }
        }
        return requests
    }

    public func begin(id: String, origin: String, nonce: String, expiresAt: Int,
                      now: Int = Int(Date().timeIntervalSince1970)) throws -> ConnectionRequest {
        try Gateway.validateOrigin(origin, allowLoopback: allowLoopback)
        _ = try path(id)
        _ = try Gateway.decodeBase64URL(nonce, count: 32)
        guard expiresAt > now && expiresAt <= now + 300 else {
            throw GatewayError.invalid("Requests must expire within five minutes.")
        }
        return try locked {
            let existing = try records(now: now)
            guard !existing.contains(where: { $0.id == id || ($0.origin == origin && $0.nonce == nonce) }) else {
                throw GatewayError.invalid("This request was already submitted.")
            }
            // ponytail: 64 retained requests and one pending per origin; revisit after real usage, not by adding a queue service.
            guard existing.count < 64,
                  !existing.contains(where: { $0.origin == origin && $0.status == .pending && $0.expires_at > now }) else {
                throw GatewayError.invalid("Too many requests. Finish the pending approval or try later.")
            }
            let request = ConnectionRequest(request_id: id, origin: origin, nonce: nonce, expires_at: expiresAt)
            try save(request)
            return request
        }
    }

    public func status(id: String, origin: String,
                       now: Int = Int(Date().timeIntervalSince1970)) throws -> ConnectionRequest {
        try Gateway.validateOrigin(origin, allowLoopback: allowLoopback)
        return try locked {
            var request = try read(id)
            guard request.origin == origin else { throw GatewayError.invalid("Request not found for this website.") }
            if request.expires_at <= now { request.status = .expired; request.proof = nil }
            return request
        }
    }

    public func pending(now: Int = Int(Date().timeIntervalSince1970)) throws -> [ConnectionRequest] {
        try locked { try records(now: now).filter { $0.status == .pending && $0.expires_at > now }
            .sorted { $0.expires_at < $1.expires_at } }
    }

    public func approve(id: String, key: Curve25519.Signing.PrivateKey,
                        now: Int = Int(Date().timeIntervalSince1970)) throws {
        try locked {
            var request = try read(id)
            request.proof = try Gateway.makeProof(request: request, key: key, now: now)
            request.status = .approved
            try save(request)
        }
    }

    public func deny(id: String) throws {
        try locked {
            var request = try read(id)
            guard request.status == .pending else { throw GatewayError.invalid("Request already completed.") }
            request.status = .denied
            try save(request)
        }
    }

    public func revoke(origin: String, now: Int = Int(Date().timeIntervalSince1970)) throws {
        try locked {
            for var request in try records(now: now) where request.origin == origin {
                request.status = .revoked; request.proof = nil; try save(request)
            }
        }
    }
}

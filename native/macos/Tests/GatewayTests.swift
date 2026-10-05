import CryptoKit
import Foundation
import XCTest
@testable import ORFGatewayCore

final class GatewayTests: XCTestCase {
    let now = 1_800_000_000
    let origin = "https://news.example"
    var nonce: String { Gateway.base64URL(Data(repeating: 42, count: 32)) }
    func mailbox(_ body: (RequestMailbox, URL) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        try body(RequestMailbox(directory: directory), directory)
    }
    func begin(_ box: RequestMailbox, origin: String = "https://news.example", nonce: String? = nil) throws -> ConnectionRequest {
        try box.begin(id: UUID().uuidString.lowercased(), origin: origin, nonce: nonce ?? self.nonce,
                      expiresAt: now + 120, now: now)
    }
    func testCanonicalOriginsAndStrictNonce() throws {
        try Gateway.validateOrigin(origin)
        try Gateway.validateOrigin("http://127.0.0.1:8765", allowLoopback: true)
        for value in ["http://news.example", "http://localhost:8765", "https://news.example/",
                      "https://news.example:443", "https://NEWS.example", "https://a..example",
                      "https://news.example?origin=other", "https://evil@news.example", "file:///tmp/a"] {
            XCTAssertThrowsError(try Gateway.validateOrigin(value), value)
        }
        XCTAssertThrowsError(try Gateway.decodeBase64URL(nonce + "=", count: 32))
        XCTAssertThrowsError(try Gateway.decodeBase64URL("A", count: 32))
    }
    func testApprovalIsRequiredAndBoundToOrigin() throws {
        try mailbox { box, directory in
            let request = try begin(box)
            XCTAssertNil(try box.status(id: request.id, origin: origin, now: now).proof)
            XCTAssertThrowsError(try box.status(id: request.id, origin: "https://shop.example", now: now))
            let key = Curve25519.Signing.PrivateKey()
            try box.approve(id: request.id, key: key, now: now)
            let approved = try box.status(id: request.id, origin: origin, now: now)
            XCTAssertEqual(approved.status, .approved)
            let proof = try XCTUnwrap(approved.proof)
            XCTAssertEqual(proof.payload.audience, origin)
            XCTAssertEqual(proof.payload.nonce, nonce)
            XCTAssertTrue(key.publicKey.isValidSignature(try Gateway.decodeBase64URL(proof.signature, count: 64),
                for: Gateway.signaturePrefix + (try Gateway.canonicalPayload(proof.payload))))
            let json = String(data: try JSONEncoder().encode(approved), encoding: .utf8)!
            for forbidden in ["profile_id", "master", "private_key", "topics", "event_log"] {
                XCTAssertFalse(json.contains(forbidden))
            }
            let attributes = try FileManager.default.attributesOfItem(atPath: directory.appendingPathComponent(request.id + ".json").path)
            XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
            XCTAssertThrowsError(try box.approve(id: request.id, key: key, now: now))
        }
    }
    func testExpiryDenyRevokeAndReplay() throws {
        try mailbox { box, _ in
            let first = try begin(box)
            XCTAssertThrowsError(try begin(box))
            XCTAssertThrowsError(try box.begin(id: "../escape", origin: origin, nonce: nonce, expiresAt: now + 1, now: now))
            XCTAssertThrowsError(try box.begin(id: UUID().uuidString.lowercased(), origin: origin, nonce: nonce, expiresAt: now + 301, now: now))
            try box.deny(id: first.id)
            XCTAssertEqual(try box.status(id: first.id, origin: origin, now: now).status, .denied)
            XCTAssertThrowsError(try begin(box)) // Completed nonce cannot be resubmitted.
            let second = try begin(box, nonce: Gateway.base64URL(Data(repeating: 43, count: 32)))
            XCTAssertThrowsError(try box.approve(id: second.id, key: Curve25519.Signing.PrivateKey(), now: now + 120))
            XCTAssertEqual(try box.status(id: second.id, origin: origin, now: now + 120).status, .expired)
            try box.approve(id: second.id, key: Curve25519.Signing.PrivateKey(), now: now)
            try box.revoke(origin: origin, now: now)
            XCTAssertNil(try box.status(id: second.id, origin: origin, now: now).proof)
            XCTAssertEqual(try box.status(id: second.id, origin: origin, now: now).status, .revoked)
        }
    }
    func testSiteKeysProduceUnrelatedSubjectsAndCrossLanguageProofBytes() throws {
        try mailbox { box, _ in
            let request = try begin(box)
            let key = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(0..<32))
            let proof = try Gateway.makeProof(request: request, key: key, now: now)
            let repeated = try Gateway.makeProof(request: request, key: key, now: now)
            XCTAssertEqual(proof.payload, repeated.payload)
            XCTAssertEqual(proof.payload.public_key, "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg")
            let fixture = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
                .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
                .appendingPathComponent("tests/fixtures/native-login-proof.json")
            let golden = try JSONDecoder().decode(LoginProof.self, from: Data(contentsOf: fixture))
            XCTAssertEqual(proof.payload, golden.payload)
            // CryptoKit may randomize its Ed25519 signatures. Verify bytes, not signature equality.
            XCTAssertTrue(key.publicKey.isValidSignature(try Gateway.decodeBase64URL(golden.signature, count: 64),
                for: Gateway.signaturePrefix + (try Gateway.canonicalPayload(proof.payload))))
            let other = try Gateway.makeProof(request: begin(box, origin: "https://shop.example"),
                key: Curve25519.Signing.PrivateKey(), now: now)
            XCTAssertNotEqual(other.payload.subject, proof.payload.subject)
            XCTAssertNotEqual(other.payload.public_key, proof.payload.public_key)
        }
    }
    func testMailboxCapacityAndCleanup() throws {
        try mailbox { box, _ in
            for index in 0..<64 { _ = try begin(box, origin: "https://s\(index).example") }
            XCTAssertThrowsError(try begin(box, origin: "https://overflow.example"))
            XCTAssertTrue(try box.pending(now: now + 420).isEmpty)
            _ = try box.begin(id: UUID().uuidString.lowercased(), origin: origin, nonce: nonce,
                expiresAt: now + 500, now: now + 420)
        }
    }

    func testNativeOperationsCannotApproveOrExportAndRejectExtraFields() throws {
        try mailbox { box, _ in
            let expiry = Int(Date().timeIntervalSince1970) + 120
            let message: [String: Any] = ["operation": "begin", "request_id": UUID().uuidString.lowercased(),
                "origin": origin, "nonce": nonce, "expires_at": expiry]
            for operation in ["approve", "deny", "export_key", "read_file", "sign"] {
                var invalid = message; invalid["operation"] = operation
                XCTAssertThrowsError(try Gateway.handle(invalid, mailbox: box))
            }
            for extra in ["profile_id", "path", "callback_url", "scopes"] {
                var invalid = message; invalid[extra] = "secret"
                XCTAssertThrowsError(try Gateway.handle(invalid, mailbox: box))
            }
            for invalidTime: Any in [true, Double(expiry) + 0.5, "\(expiry)"] {
                var invalid = message; invalid["expires_at"] = invalidTime
                XCTAssertThrowsError(try Gateway.handle(invalid, mailbox: box))
            }
            XCTAssertEqual(try Gateway.handle(message, mailbox: box)["status"] as? String, "pending")
            let status: [String: Any] = ["operation": "status", "request_id": message["request_id"]!, "origin": origin]
            XCTAssertNil(try Gateway.handle(status, mailbox: box)["proof"])
        }
    }
}

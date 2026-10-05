import CryptoKit
import Security
import SwiftUI
#if SWIFT_PACKAGE
import ORFGatewayCore
#endif

private enum SiteKeys {
    static let service = "org.openrecommender.gateway.site-identity.v1"
    static func query(_ origin: String? = nil) -> [String: Any] {
        var query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrSynchronizable as String: false]
        if let origin { query[kSecAttrAccount as String] = origin }
        return query
    }
    static func key(for origin: String) throws -> Curve25519.Signing.PrivateKey {
        var lookup = query(origin)
        lookup[kSecReturnData as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &result)
        if status == errSecSuccess, let data = result as? Data {
            return try Curve25519.Signing.PrivateKey(rawRepresentation: data)
        }
        guard status == errSecItemNotFound else { throw GatewayError.invalid("Keychain access failed (\(status)).") }
        let key = Curve25519.Signing.PrivateKey()
        var attributes = query(origin)
        attributes[kSecValueData as String] = key.rawRepresentation
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        attributes[kSecAttrLabel as String] = "ORF identity for \(origin)"
        let saved = SecItemAdd(attributes as CFDictionary, nil)
        guard saved == errSecSuccess else { throw GatewayError.invalid("Cannot save this site's identity (\(saved)).") }
        return key
    }
    static func origins() throws -> [String] {
        var lookup = query()
        lookup[kSecReturnAttributes as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitAll
        var result: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &result)
        if status == errSecItemNotFound { return [] }
        guard status == errSecSuccess, let items = result as? [[String: Any]] else {
            throw GatewayError.invalid("Cannot list Keychain identities (\(status)).")
        }
        return items.compactMap { $0[kSecAttrAccount as String] as? String }.sorted()
    }
    static func forget(_ origin: String) throws {
        let status = SecItemDelete(query(origin) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw GatewayError.invalid("Cannot remove this identity (\(status)).")
        }
    }
}

@MainActor
final class GatewayModel: ObservableObject {
    @Published var pending: [ConnectionRequest] = []
    @Published var origins: [String] = []
    @Published var error: String?
    private var mailbox: RequestMailbox?
    init() {
        do { mailbox = try RequestMailbox(directory: Gateway.sharedDirectory()); refresh() }
        catch { self.error = error.localizedDescription }
    }
    func refresh() {
        do { pending = try mailbox?.pending() ?? []; origins = try SiteKeys.origins() }
        catch { self.error = error.localizedDescription }
    }
    func approve(_ request: ConnectionRequest) {
        perform { try mailbox?.approve(id: request.id, key: SiteKeys.key(for: request.origin)) }
    }
    func deny(_ request: ConnectionRequest) { perform { try mailbox?.deny(id: request.id) } }
    func forget(_ origin: String) {
        perform { try mailbox?.revoke(origin: origin); try SiteKeys.forget(origin) }
    }
    private func perform(_ operation: () throws -> Void) {
        do { try operation(); error = nil; refresh() }
        catch { self.error = error.localizedDescription }
    }
}

@main
struct ORFGatewayApp: App {
    @StateObject private var model = GatewayModel()
    @State private var forgetting: String?
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()
    var body: some Scene {
        WindowGroup {
            VStack(alignment: .leading, spacing: 18) {
                Label("ORF Gateway", systemImage: "hand.raised.shield.fill").font(.largeTitle.bold())
                Text("Your identity. A different key for every website.").font(.title3)
                Text("Login preview only. No profile files, preference history, or private keys are sent to websites.")
                    .foregroundStyle(.secondary)
                if let error = model.error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                List {
                    Section("Requests awaiting your approval") {
                        if model.pending.isEmpty { Text("Click Connect on a website, then review its request here.") }
                        ForEach(model.pending) { request in
                            VStack(alignment: .leading, spacing: 10) {
                                Text(request.origin).font(.headline).textSelection(.enabled)
                                Text("Prove control of your identity for this website. No preferences are shared.")
                                Text("Expires \(Date(timeIntervalSince1970: Double(request.expires_at)), style: .relative)")
                                    .font(.caption).foregroundStyle(.secondary)
                                HStack {
                                    Button("Deny") { model.deny(request) }
                                    Button("Approve login") { model.approve(request) }.buttonStyle(.borderedProminent)
                                }
                            }.padding(.vertical, 8)
                        }
                    }
                    Section("Site-specific identities on this Mac") {
                        ForEach(model.origins, id: \.self) { origin in
                            HStack {
                                Text(origin).textSelection(.enabled)
                                Spacer()
                                Button("Forget identity", role: .destructive) { forgetting = origin }
                            }
                        }
                    }
                }
                Text("Forgetting stops pending ORF proofs and removes this Mac's key. It cannot log you out of a website or erase a proof already delivered. These keys are not backed up yet.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(24).frame(minWidth: 640, minHeight: 520)
                .onReceive(timer) { _ in model.refresh() }
                .confirmationDialog("Forget \(forgetting ?? "this website")? You may lose access to its existing account.",
                    isPresented: Binding(get: { forgetting != nil }, set: { if !$0 { forgetting = nil } })) {
                    Button("Forget identity", role: .destructive) {
                        if let origin = forgetting { model.forget(origin) }; forgetting = nil
                    }
                }
        }
    }
}

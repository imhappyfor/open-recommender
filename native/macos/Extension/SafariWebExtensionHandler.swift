import Foundation
import SafariServices

final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        let response = NSExtensionItem()
        do {
            guard let item = context.inputItems.first as? NSExtensionItem,
                  let message = item.userInfo?[SFExtensionMessageKey] as? [String: Any] else {
                throw GatewayError.invalid("Invalid native message.")
            }
            let mailbox = try RequestMailbox(directory: Gateway.sharedDirectory(), allowLoopback: Gateway.allowLoopback)
            response.userInfo = [SFExtensionMessageKey: try Gateway.handle(message, mailbox: mailbox)]
        } catch {
            // Never return file paths, Keychain details, or arbitrary native exception text to a page.
            response.userInfo = [SFExtensionMessageKey: Gateway.publicError]
        }
        context.completeRequest(returningItems: [response], completionHandler: nil)
    }
}

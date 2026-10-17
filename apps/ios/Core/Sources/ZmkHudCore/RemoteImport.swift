import CryptoKit
import Foundation

/// Remote mode's pairing (host/remote.py, docs/touch.md), the app's side of it: the key proved
/// without being sent, and the computer's proof back.
public enum RemotePairing {
    public static let protocolName = "zmkhud-remote/1"
    public static let welcomeText = "\(protocolName) welcome"

    /// The key as the link writes it: 43 base64url characters, 32 bytes.
    public static func key(_ text: String) -> Data? {
        guard text.count == 43, text.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }) else {
            return nil
        }
        let b64 = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/") + "="
        return Data(base64Encoded: b64).flatMap { $0.count == 32 ? $0 : nil }
    }

    static func hex<D: Sequence>(_ bytes: D) -> String where D.Element == UInt8 { bytes.map { String(format: "%02x", $0) }.joined() }

    public static func kid(_ key: Data) -> String { String(hex(SHA256.hash(data: key)).prefix(16)) }

    public static func mac(_ key: Data, _ text: String) -> String {
        hex(HMAC<SHA256>.authenticationCode(for: Data(text.utf8), using: SymmetricKey(data: key)))
    }

    public static func authText(nonce: String, cnonce: String) -> String { "\(protocolName) auth \(nonce) \(cnonce)" }

    public static func sessionKey(_ key: Data, nonce: String, cnonce: String) -> Data {
        Data(HMAC<SHA256>.authenticationCode(for: Data("\(protocolName) session \(nonce) \(cnonce)".utf8),
                                             using: SymmetricKey(data: key)))
    }
}

/// A WebSocket's text messages, as RemoteImport uses them (URLSessionChannel, or a test's server).
public protocol MessageChannel: AnyObject {
    func send(_ text: String) async throws
    func receive() async throws -> String
    func close()
}

/// The keymap, fetched from a computer in remote mode: `zmk-layer-hud remote on --app` prints a link,
/// zmkhud://import?from=HOST:PORT#k=KEY, that the app opens. It pairs as the remote page does, takes
/// the keymap the computer sends once paired, and goes.
public enum RemoteImport {
    public struct Link: Equatable, Sendable {
        public let host: String
        public let key: Data
    }

    public enum Failure: Error, Equatable {
        case notALink
        /// The computer holds another key: the link is from before `remote unpair`.
        case anotherCode
        /// The computer could not prove the key: not the one the link came from.
        case notThatComputer
        case noKeymap
    }

    public static func link(_ url: URL) -> Link? {
        guard url.scheme == "zmkhud", url.host == "import",
              let from = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "from" })?.value,
              from.range(of: #"^(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+):[0-9]{1,5}$"#, options: .regularExpression) != nil,
              let fragment = url.fragment, fragment.hasPrefix("k="),
              let key = RemotePairing.key(String(fragment.dropFirst(2))) else { return nil }
        return Link(host: from, key: key)
    }

    static func object(_ text: String) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any]
    }

    /// The keymap message's text, as the computer sent it.
    public static func fetch(_ link: Link, over channel: MessageChannel, cnonce: String? = nil) async throws -> String {
        defer { channel.close() }
        guard let challenge = object(try await channel.receive()), challenge["kind"] as? String == "challenge",
              let nonce = challenge["nonce"] as? String else { throw Failure.notThatComputer }
        guard challenge["kid"] as? String == RemotePairing.kid(link.key) else { throw Failure.anotherCode }
        let mine = cnonce ?? RemotePairing.hex((0..<16).map { _ in UInt8.random(in: 0...255) })
        let auth: [String: Any] = ["kind": "auth", "v": 1, "cnonce": mine,
                                   "mac": RemotePairing.mac(link.key, RemotePairing.authText(nonce: nonce, cnonce: mine))]
        try await channel.send(String(decoding: try JSONSerialization.data(withJSONObject: auth), as: UTF8.self))
        let session = RemotePairing.sessionKey(link.key, nonce: nonce, cnonce: mine)
        guard let welcome = object(try await channel.receive()), welcome["kind"] as? String == "welcome",
              welcome["mac"] as? String == RemotePairing.mac(session, RemotePairing.welcomeText) else {
            throw Failure.notThatComputer
        }
        // The keymap comes first; what else the computer says (secure, typing) is not the app's.
        for _ in 0..<8 {
            let text = try await channel.receive()
            if object(text)?["kind"] as? String == "keymap" { return text }
        }
        throw Failure.noKeymap
    }

    /// The computer's socket, as the remote page opens it: its own address, its page's Origin.
    public static func open(_ link: Link) -> MessageChannel { URLSessionChannel(host: link.host) }
}

/// A WebSocket over URLSession.
final class URLSessionChannel: MessageChannel {
    private let task: URLSessionWebSocketTask

    init(host: String) {
        var request = URLRequest(url: URL(string: "ws://\(host)/ws")!)
        request.setValue("http://\(host)", forHTTPHeaderField: "Origin")
        request.timeoutInterval = 10
        task = URLSession.shared.webSocketTask(with: request)
        task.maximumMessageSize = 8 * 1024 * 1024
        task.resume()
    }

    func send(_ text: String) async throws { try await task.send(.string(text)) }

    func receive() async throws -> String {
        switch try await task.receive() {
        case let .string(s): return s
        case let .data(d): return String(decoding: d, as: UTF8.self)
        @unknown default: return ""
        }
    }

    func close() { task.cancel(with: .normalClosure, reason: nil) }
}

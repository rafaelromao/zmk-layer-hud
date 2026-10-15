import SwiftUI
import UniformTypeIdentifiers
import ZmkHudCore

/// The app the keyboard comes in: how to turn it on, the keymap it draws -- imported from Files, a
/// file opened with the app (AirDrop, "Open in"), or fetched from a computer in remote mode through
/// the link `zmk-layer-hud remote on --app` prints -- and a field to try it in.
@main
struct ZmkHudApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

struct ContentView: View {
    @State private var importing = false
    @State private var status = Keymaps.current()
    @State private var said = ""
    @State private var trial = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Turn it on") {
                    Text("Settings › General › Keyboard › Keyboards › Add New Keyboard… › ZMK Layer HUD. "
                         + "It needs no Full Access: it sends nothing anywhere.")
                    Button("Open Settings") {
                        if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                    }
                }
                Section("Keymap") {
                    Text(status)
                    if !said.isEmpty { Text(said).foregroundStyle(.secondary) }
                    Button("Import a keymap…") { importing = true }
                    Text("On the computer: zmk-layer-hud remote on --app, and open its link here; or "
                         + "zmk-layer-hud keymap -o board.json, then AirDrop it here or save it to Files.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("Try it") {
                    TextField("Switch to the keyboard with 🌐 and type here", text: $trial, axis: .vertical)
                }
                Section {
                    Text("A keyboard extension cannot send ⌘ or Ctrl shortcuts, Escape, up or down, or page keys, and "
                         + "password and phone fields use the system keyboard.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("ZMK Layer HUD")
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.json, .data]) { result in
            if case let .success(url) = result { take(url) }
        }
        .onOpenURL { url in
            if let link = RemoteImport.link(url) {
                said = "Fetching the keymap from \(link.host)…"
                Task { @MainActor in
                    said = await Keymaps.fetch(link)
                    status = Keymaps.current()
                }
            } else {
                take(url)
            }
        }
    }

    private func take(_ url: URL) {
        said = Keymaps.importFile(url)
        status = Keymaps.current()
    }
}

/// The keymap in the App Group's container, where the keyboard reads it.
enum Keymaps {
    static var container: URL? {
        guard let group = Bundle.main.object(forInfoDictionaryKey: "ZMKHUDAppGroup") as? String else { return nil }
        return FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)
    }

    static func current() -> String {
        guard let url = container?.appendingPathComponent(KeymapFile.name), let data = try? KeymapFile.read(url),
              case let .ok(title, _) = KeymapFile.check(data) else { return "No keymap yet: import one." }
        return "Keymap: \(title)"
    }

    /// Reads, checks and keeps a keymap file; says how it went.
    static func importFile(_ url: URL) -> String {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? KeymapFile.read(url) else { return "That file could not be read." }
        return keep(data)
    }

    /// The keymap a computer in remote mode sends once paired (ZmkHudCore.RemoteImport).
    static func fetch(_ link: RemoteImport.Link) async -> String {
        do {
            return keep(Data(try await RemoteImport.fetch(link, over: RemoteImport.open(link)).utf8))
        } catch RemoteImport.Failure.anotherCode {
            return "That computer has paired again since this link: run zmk-layer-hud remote on --app there for a new one."
        } catch RemoteImport.Failure.notThatComputer {
            return "The computer at \(link.host) is not the one that link is from."
        } catch {
            return "Could not reach \(link.host): is remote mode on there, and this iPad on the same network?"
        }
    }

    static func keep(_ data: Data) -> String {
        switch KeymapFile.check(data) {
        case let .refused(why):
            return why
        case let .ok(title, types):
            guard let dir = container else { return "The app has no App Group to share the keymap with its keyboard." }
            do {
                try data.write(to: dir.appendingPathComponent(KeymapFile.name), options: .atomic)
            } catch {
                return "The keymap could not be kept: \(error.localizedDescription)"
            }
            return types ? "Imported \(title). The keyboard draws it the next time it opens."
                : "Imported \(title), but it says nothing of what its keys do, so the keyboard only draws it: "
                + "write it with zmk-layer-hud keymap -o."
        }
    }
}

// Nightdesk.app: the Mac window around the engine.
//
// What this small program does:
//   1. Opens a window and a menu-bar item.
//   2. Makes sure the engine (the Python program that watches prices and trades the practice
//      account) is running: attaches to one that is already going, or starts one.
//   3. Shows the engine's screens in the window.
//   4. Closing the window keeps everything working in the menu bar. Quitting stops the engine.
//
// Build:  shell/build.sh      (uses Apple's Swift compiler; no Xcode project needed)
import Cocoa
import WebKit

let configuredPort = Int(ProcessInfo.processInfo.environment["NIGHTDESK_PORT"] ?? "8791") ?? 8791
let port = (1...65535).contains(configuredPort) ? configuredPort : 8791
let origin = "http://127.0.0.1:\(port)"
let night = NSColor(srgbRed: 0x0E / 255.0, green: 0x14 / 255.0, blue: 0x20 / 255.0, alpha: 1)

func projectHome() -> URL {
    if let env = ProcessInfo.processInfo.environment["NIGHTDESK_PROJECT_DIR"] { return URL(fileURLWithPath: env) }
    return Bundle.main.bundleURL.deletingLastPathComponent()
}

func runtimeHome() -> URL {
    if let env = ProcessInfo.processInfo.environment["NIGHTDESK_HOME"] { return URL(fileURLWithPath: env) }
    let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
    return support.appendingPathComponent("Nightdesk")
}

func argument(_ name: String) -> String? {
    let args = CommandLine.arguments
    if let i = args.firstIndex(of: name), i + 1 < args.count { return args[i + 1] }
    return nil
}

/// The top strip of the window is web content, so it needs help to behave like a title bar.
final class DragStrip: NSView {
    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 { window?.performZoom(nil) } else { window?.performDrag(with: event) }
    }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// Lets the self-test place the window off-screen without macOS dragging it back.
final class FreeWindow: NSWindow {
    var pinned = false
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect {
        pinned ? frameRect : super.constrainFrameRect(frameRect, to: screen)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate {
    var window: FreeWindow!
    var web: WKWebView!
    var statusItem: NSStatusItem?
    var engine: Process?
    var healthTimer: Timer?
    var statusTimer: Timer?
    var showingApp = false
    var triedToStart = false
    var waited = 0.0
    var autoTrade = true
    var confirmedQuit = false
    let home = projectHome()
    let dataHome = runtimeHome()
    let snapshotPath = argument("--snapshot")
    let snapshotView = argument("--view")
    let menuAccount = NSMenuItem(title: "Account: waiting for the engine", action: nil, keyEquivalent: "")
    let menuTonight = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let menuMarket = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let menuToggle = NSMenuItem(title: "Pause Auto-Trading", action: #selector(toggleAuto), keyEquivalent: "")

    var testing: Bool { snapshotPath != nil }

    // ------------------------------------------------------------------ launch
    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenus()
        buildWindow()
        if !testing { buildStatusItem() }
        web.loadHTMLString(page(title: "Starting Nightdesk.", body: "Opening your local practice workspace. On first launch, choose whether to download prices and train your own model."), baseURL: nil)
        healthTimer = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in self?.checkEngine() }
        checkEngine()
        if !testing {
            statusTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.refreshStatus() }
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    func buildWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1320, height: 860)
        window = FreeWindow(contentRect: frame, styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                            backing: .buffered, defer: false)
        window.title = "Nightdesk"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.backgroundColor = night
        window.minSize = NSSize(width: 1040, height: 680)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.appearance = NSAppearance(named: .darkAqua)
        window.setFrameAutosaveName("NightdeskMain")

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        if testing {                                            // self-test: collect any script errors so they can be reported
            let probe = "window.__errs=[];addEventListener('error',e=>__errs.push(e.message+' @'+(e.filename||'').split('/').pop()+':'+e.lineno));addEventListener('unhandledrejection',e=>__errs.push('rejection: '+e.reason));"
            config.userContentController.addUserScript(WKUserScript(source: probe, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        web = WKWebView(frame: frame, configuration: config)
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")          // no white flash before the page paints
        web.autoresizingMask = [.width, .height]
        web.allowsMagnification = false

        let holder = NSView(frame: frame)
        holder.wantsLayer = true
        holder.layer?.backgroundColor = night.cgColor
        holder.addSubview(web)
        let strip = DragStrip(frame: NSRect(x: 0, y: frame.height - 46, width: frame.width, height: 46))
        strip.autoresizingMask = [.width, .minYMargin]
        holder.addSubview(strip)
        window.contentView = holder

        if testing {
            window.pinned = true
            window.alphaValue = 0                               // self-test: render without showing anything
            window.ignoresMouseEvents = true
            window.setFrame(NSRect(x: -6000, y: 200, width: 1320, height: 860), display: false)
            window.orderFrontRegardless()
        } else {
            if !window.setFrameUsingName("NightdeskMain") { window.center() }
            window.makeKeyAndOrderFront(nil)
        }
    }

    func buildMenus() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Nightdesk", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Nightdesk", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Nightdesk", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        viewItem.submenu = view

        let windowItem = NSMenuItem(); main.addItem(windowItem)
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Show Nightdesk", action: #selector(showWindow), keyEquivalent: "0")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowItem.submenu = win
        NSApp.windowsMenu = win

        let helpItem = NSMenuItem(); main.addItem(helpItem)
        let help = NSMenu(title: "Help")
        help.addItem(withTitle: "How Nightdesk Works", action: #selector(openExplainer), keyEquivalent: "?")
        help.addItem(withTitle: "Show the Engine Log", action: #selector(openLog), keyEquivalent: "")
        helpItem.submenu = help
        NSApp.mainMenu = main
    }

    func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            let image = NSImage(systemSymbolName: "lamp.desk", accessibilityDescription: "Nightdesk")
                ?? NSImage(systemSymbolName: "moon.stars", accessibilityDescription: "Nightdesk")
            image?.isTemplate = true
            button.image = image
            button.imagePosition = .imageLeading
            button.font = NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize(for: .small), weight: .regular)
        }
        let menu = NSMenu()
        menu.addItem(withTitle: "Open Nightdesk", action: #selector(showWindow), keyEquivalent: "")
        menu.addItem(.separator())
        for info in [menuAccount, menuTonight, menuMarket] { info.isEnabled = false; menu.addItem(info) }
        menu.addItem(.separator())
        menuToggle.target = self
        menu.addItem(menuToggle)
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Nightdesk", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        item.menu = menu
        statusItem = item
    }

    // ------------------------------------------------------------------ the engine
    func request(_ path: String, body: [String: Any]? = nil, timeout: TimeInterval = 1.5, done: @escaping (Data?) -> Void) {
        var req = URLRequest(url: URL(string: origin + path)!, timeoutInterval: timeout)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        if let body = body {
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.setValue("1", forHTTPHeaderField: "X-Nightdesk")
            req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        URLSession.shared.dataTask(with: req) { data, response, _ in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(ok ? data : nil) }
        }.resume()
    }

    func checkEngine() {
        request("/api/health") { [weak self] data in
            guard let self = self else { return }
            if let data = data, let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any], json["app"] as? String == "nightdesk" {
                self.waited = 0
                if !self.showingApp {
                    self.showingApp = true
                    self.web.load(URLRequest(url: URL(string: origin + "/")!))
                    self.refreshStatus()
                    if self.testing { self.scheduleSnapshot() }
                }
                return
            }
            if self.showingApp {                                  // it was up and has gone away
                self.showingApp = false
                self.web.loadHTMLString(self.page(title: "The engine stopped.", body: "Trying to start it again."), baseURL: nil)
            }
            self.waited += 0.6
            if self.testing { if self.waited > 20 { exit(3) }; return }
            if !self.triedToStart && self.waited > 1.5 { self.triedToStart = true; self.startEngine() }
            if self.waited > 30 {
                self.web.loadHTMLString(self.page(title: "The engine didn't start.", body: "Run Install.command in your Nightdesk folder. If another app is using this port, set NIGHTDESK_PORT to a free port. Choose Help, then Show the Engine Log for details."), baseURL: nil)
                self.healthTimer?.invalidate()
            }
        }
    }

    func startEngine() {
        let python = home.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            web.loadHTMLString(page(title: "Install Nightdesk first.", body: "Keep Nightdesk.app beside the engine and .venv folders. Open Install.command, then Launch.command. Moving the whole Nightdesk folder is supported."), baseURL: nil)
            return
        }
        let log = dataHome.appendingPathComponent("logs/engine.log")
        try? FileManager.default.createDirectory(at: log.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil) }
        let process = Process()
        process.executableURL = python
        process.arguments = ["-u", "-m", "nightdesk"]
        process.currentDirectoryURL = home
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = home.appendingPathComponent("engine").path
        process.environment = env
        if let handle = try? FileHandle(forWritingTo: log) {
            handle.seekToEndOfFile()
            process.standardOutput = handle
            process.standardError = handle
        }
        do { try process.run(); engine = process } catch {
            web.loadHTMLString(page(title: "The engine wouldn't start.", body: error.localizedDescription), baseURL: nil)
        }
    }

    func stopEngine() {
        guard showingApp else {
            if let process = engine, process.isRunning { process.terminate() }
            return
        }
        let group = DispatchGroup()
        group.enter()
        var req = URLRequest(url: URL(string: origin + "/api/control")!, timeoutInterval: 2)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("1", forHTTPHeaderField: "X-Nightdesk")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["action": "shutdown"])
        URLSession.shared.dataTask(with: req) { _, _, _ in group.leave() }.resume()
        _ = group.wait(timeout: .now() + 2.5)
        if let process = engine, process.isRunning { process.terminate() }
    }

    func refreshStatus() {
        guard showingApp, statusItem != nil else { return }
        request("/api/state") { [weak self] data in
            guard let self = self, let button = self.statusItem?.button else { return }
            guard let data = data, let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let account = json["account"] as? [String: Any], let clock = json["clock"] as? [String: Any],
                  let eng = json["engine"] as? [String: Any] else {
                button.title = ""
                self.menuAccount.title = "Engine not answering"
                self.menuTonight.title = ""; self.menuMarket.title = ""
                return
            }
            let money = NumberFormatter(); money.numberStyle = .currency; money.currencyCode = "USD"; money.locale = Locale(identifier: "en_US")
            let equity = account["equity"] as? Double ?? 0, day = account["day_pnl"] as? Double ?? 0
            let phase = clock["phase"] as? String ?? ""
            self.autoTrade = eng["auto_trade"] as? Bool ?? true
            let dayText = (day >= 0 ? "+" : "\u{2212}") + (money.string(from: NSNumber(value: abs(day))) ?? "")
            money.maximumFractionDigits = 0
            button.title = phase == "open" ? " " + (day >= 0 ? "+" : "\u{2212}") + (money.string(from: NSNumber(value: abs(day))) ?? "") : ""
            money.maximumFractionDigits = 2
            self.menuAccount.title = "Practice account: " + (money.string(from: NSNumber(value: equity)) ?? "")
            self.menuTonight.title = (phase == "open" ? "So far tonight: " : "Last session: ") + dayText
            let openTime = clock["session_open"] as? String ?? ""
            let openDate = ISO8601DateFormatter().date(from: openTime)
            let local = DateFormatter()
            local.dateFormat = "EEE h:mm a z"
            let localOpen = openDate.map { local.string(from: $0) } ?? openTime
            self.menuMarket.title = phase == "open" ? "New York is open" : "New York is closed (opens \(localOpen))"
            self.menuToggle.title = self.autoTrade ? "Pause Auto-Trading" : "Resume Auto-Trading"
        }
    }

    // ------------------------------------------------------------------ actions
    @objc func showWindow() {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }
    @objc func reload() { if showingApp { web.reload() } }
    @objc func toggleAuto() {
        guard showingApp else { return }
        request("/api/control", body: ["action": autoTrade ? "pause" : "resume"]) { [weak self] _ in self?.refreshStatus() }
    }
    @objc func openExplainer() {
        guard showingApp else { return }
        request("/api/open", body: ["what": "explainer"]) { _ in }
    }
    @objc func openLog() { NSWorkspace.shared.open(dataHome.appendingPathComponent("logs/engine.log")) }

    // ------------------------------------------------------------------ window and quitting
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)                                      // keep working in the menu bar
        return false
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
        return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if testing || confirmedQuit { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "Quit Nightdesk?"
        alert.informativeText = "The engine stops watching the market until you open Nightdesk again. Your practice account is saved.\n\nTo keep it working, close the window instead. It stays in the menu bar."
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Keep Running")
        if alert.runModal() != .alertFirstButtonReturn { return .terminateCancel }
        confirmedQuit = true
        stopEngine()
        return .terminateNow
    }

    // ------------------------------------------------------------------ web view
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" || url.host == "127.0.0.1" { decisionHandler(.allow); return }
        if url.scheme == "http" || url.scheme == "https" { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { showingApp = false }

    func page(title: String, body: String) -> String {
        let safe = { (s: String) in s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;") }
        return """
        <!doctype html><meta charset="utf-8"><style>
        html,body{height:100%;margin:0;background:#0E1420;color:#ECE8DD;font:16px/1.5 system-ui;-webkit-user-select:none;cursor:default}
        main{height:100%;display:grid;place-content:center;justify-items:start;padding:0 12vw}
        i{width:10px;height:10px;border-radius:50%;background:#F2B24C;box-shadow:0 0 0 4px rgb(242 178 76/.16),0 0 18px 4px rgb(242 178 76/.35);margin-bottom:22px}
        h1{font:500 34px/1.1 ui-serif,"New York",Georgia,serif;margin:0 0 10px} p{color:#B4BCCD;max-width:56ch;margin:0}
        </style><main><i></i><h1>\(safe(title))</h1><p>\(safe(body))</p></main>
        """
    }

    // ------------------------------------------------------------------ self-test (used by the build to prove the window renders)
    func scheduleSnapshot() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 5.0) {
            if let view = self.snapshotView {
                self.web.evaluateJavaScript("document.querySelector('[data-nav=\"\(view)\"]')?.click()", completionHandler: nil)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
                let report = "JSON.stringify({sections: document.querySelectorAll('#nav button').length, heading: (document.querySelector('#view h1')||{}).innerText||'', words: document.querySelector('#view').innerText.length, errors: window.__errs||[]})"
                self.web.evaluateJavaScript(report) { result, _ in print("self-test:", result as? String ?? "no answer") }
                self.web.takeSnapshot(with: nil) { image, _ in
                    guard let image = image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                          let png = rep.representation(using: .png, properties: [:]), let path = self.snapshotPath else { exit(2) }
                    try? png.write(to: URL(fileURLWithPath: path))
                    exit(0)
                }
            }
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(delegate.testing ? .accessory : .regular)
app.run()

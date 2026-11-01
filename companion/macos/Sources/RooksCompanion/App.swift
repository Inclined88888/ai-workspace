import SwiftUI
import AppKit
import CompanionCore

@main
struct RooksCompanionApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        Settings { EmptyView() }
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    var panel: NSPanel?
    var statusItem: NSStatusItem?
    var hostView: NSHostingView<AnyView>?
    let stateStore = StateStore.shared

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        setupStatusItem()
        Task { @MainActor in
            await setupPanel()
            await stateStore.connect()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        Task { await stateStore.disconnect() }
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        guard let button = statusItem?.button else { return }
        button.image = NSImage(systemSymbolName: "circle.dotted", accessibilityDescription: "Rooks Companion")
        button.image?.isTemplate = true

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Show Companion", action: #selector(togglePanel), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Open Rooks", action: #selector(openRooks), keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        let launchItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLaunchAtLogin), keyEquivalent: "")
        launchItem.state = LaunchAtLogin.isEnabled ? .on : .off
        menu.addItem(launchItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(quit), keyEquivalent: "q"))
        statusItem?.menu = menu
    }

    @MainActor
    private func setupPanel() async {
        let viewModel = PanelViewModel(stateStore: stateStore)
        let contentView = CompanionPanelView()
            .environmentObject(viewModel)
        let hostingView = NSHostingView(rootView: AnyView(contentView))
        hostingView.frame = NSRect(x: 0, y: 0, width: 360, height: 48)
        self.hostView = hostingView

        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 360, height: 48),
            styleMask: [.nonactivatingPanel, .borderless, .hudWindow],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        panel.contentView = hostingView
        panel.animationBehavior = .utilityWindow
        self.panel = panel

        positionPanel()
        panel.orderFront(nil)
    }

    private func positionPanel() {
        guard let panel = panel, let screen = NSScreen.main else { return }

        let safeArea = screen.safeAreaInsets
        let hasNotch = safeArea.top > (screen.auxiliaryTopLeftArea?.height ?? 0)

        let panelWidth: CGFloat = 360
        let panelHeight: CGFloat = 48 // collapsed height

        var x: CGFloat
        if hasNotch {
            // Center under the notch
            let notchCenter = screen.frame.midX
            x = notchCenter - panelWidth / 2
        } else {
            // Top-center under menu bar
            x = screen.frame.midX - panelWidth / 2
        }

        let y = screen.frame.maxY - safeArea.top - panelHeight

        panel.setFrame(NSRect(x: x, y: y, width: panelWidth, height: panelHeight), display: true)
    }

    @objc func togglePanel() {
        guard let panel = panel else { return }
        if panel.isVisible {
            panel.orderOut(nil)
        } else {
            positionPanel()
            panel.orderFront(nil)
        }
    }

    @objc func openRooks() {
        NSWorkspace.shared.open(URL(string: "rooks://")!)
    }

    @objc func toggleLaunchAtLogin(_ sender: NSMenuItem) {
        let newState = !LaunchAtLogin.isEnabled
        do {
            try LaunchAtLogin.setEnabled(newState)
            sender.state = newState ? .on : .off
        } catch {
            print("Failed to toggle launch at login: \(error)")
        }
    }

    @objc func quit() {
        NSApp.terminate(nil)
    }
}

// MARK: - Launch at Login Helper

enum LaunchAtLogin {
    static var isEnabled: Bool {
        if #available(macOS 13.0, *) {
            return SMAppService.mainApp.status == .enabled
        }
        return false
    }

    static func setEnabled(_ enabled: Bool) throws {
        if #available(macOS 13.0, *) {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        }
    }
}

import ServiceManagement
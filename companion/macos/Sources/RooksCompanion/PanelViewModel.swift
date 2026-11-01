import SwiftUI
import Combine
import CompanionCore

@MainActor
final class PanelViewModel: ObservableObject {
    @Published var state: CompanionState?
    @Published var isExpanded = false
    @Published var isServerRunning = false
    @Published var errorMessage: String?
    @Published var showStartButton = false

    private let stateStore: StateStore
    private var listenerID: UUID?
    private var cancellables = Set<AnyCancellable>()

    init(stateStore: StateStore) {
        self.stateStore = stateStore
        setupListener()
        checkServerStatus()
    }

    deinit {
        if let id = listenerID {
            Task.detached { [stateStore] in
                await stateStore.removeListener(id)
            }
        }
    }

    private func setupListener() {
        Task { [weak self] in
            guard let self = self else { return }
            self.listenerID = await self.stateStore.addListener { [weak self] newState in
                Task { @MainActor [weak self] in
                    guard let self = self else { return }
                    let oldRequestsCount = self.state?.requests.count ?? 0
                    self.state = newState
                    self.isServerRunning = true
                    self.errorMessage = nil
                    self.showStartButton = false
                    // Auto-expand briefly on new request
                    if newState.requests.count > oldRequestsCount {
                        self.expandTemporarily()
                    }
                }
            }
            // Give it a moment to register, then fetch initial state
            try? await Task.sleep(nanoseconds: 100_000_000)
            await self.refreshState()
        }
    }

    private func checkServerStatus() {
        Task { [weak self] in
            do {
                let config = try await CompanionConfigStore.shared.loadConfig()
                let client = CompanionAPIClient(baseURL: config.url, token: config.token)
                _ = try await client.getState()
                await MainActor.run {
                    self?.isServerRunning = true
                    self?.errorMessage = nil
                    self?.showStartButton = false
                }
            } catch {
                await MainActor.run {
                    self?.isServerRunning = false
                    self?.errorMessage = error.localizedDescription
                    self?.showStartButton = true
                }
            }
        }
    }

    func refreshState() async {
        do {
            let config = try await CompanionConfigStore.shared.loadConfig(forceReload: true)
            let client = CompanionAPIClient(baseURL: config.url, token: config.token)
            let freshState = try await client.getState()
            await MainActor.run {
                self.state = freshState
                self.isServerRunning = true
                self.errorMessage = nil
                self.showStartButton = false
            }
        } catch {
            await MainActor.run {
                self.isServerRunning = false
                self.errorMessage = error.localizedDescription
                self.showStartButton = true
            }
        }
    }

    func toggleExpanded() {
        isExpanded.toggle()
    }

    func collapse() {
        isExpanded = false
    }

    func expandTemporarily() {
        isExpanded = true
        // Play subtle sound
        NSSound.beep()
        // Auto-collapse after 8 seconds if no interaction
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
            guard let self = self, self.isExpanded else { return }
            // Only collapse if no pending requests remain
            if self.state?.requests.isEmpty ?? true {
                self.collapse()
            }
        }
    }

    func startServer() {
        // Try to start AI Workspace app
        let workspaceURL = URL(fileURLWithPath: "/Applications/AI Workspace.app")
        if NSWorkspace.shared.runningApplications.contains(where: { $0.bundleURL == workspaceURL }) {
            // Already running, just refresh
            Task { await refreshState() }
            return
        }

        if FileManager.default.fileExists(atPath: workspaceURL.path) {
            NSWorkspace.shared.open(workspaceURL)
        } else if let aiWorkspaceCLI = NSString(string: "~/.local/bin/ai-workspace").expandingTildeInPath as String?,
                  FileManager.default.fileExists(atPath: aiWorkspaceCLI) {
            let task = Process()
            task.executableURL = URL(fileURLWithPath: aiWorkspaceCLI)
            try? task.run()
        } else {
            errorMessage = "Could not find AI Workspace. Install it first."
            return
        }

        // Poll for server to come up
        Task { [weak self] in
            for _ in 0..<30 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                await self?.refreshState()
                if self?.isServerRunning == true { break }
            }
        }
    }

    // MARK: - Actions

    func decideRequest(_ request: CompanionRequest, decision: Decision, answer: String? = nil) async {
        do {
            _ = try await stateStore.decideRequest(id: request.id, decision: decision, answer: answer)
        } catch {
            await MainActor.run {
                self.errorMessage = error.localizedDescription
            }
        }
    }

    func dropFile(url: URL, question: String = "") async {
        do {
            let data = try Data(contentsOf: url)
            let response = try await stateStore.dropFile(name: url.lastPathComponent, question: question, data: data)
            if let openURL = URL(string: response.open) {
                NSWorkspace.shared.open(openURL)
            }
        } catch {
            await MainActor.run {
                self.errorMessage = error.localizedDescription
            }
        }
    }

    func deleteRule(at index: Int) async {
        do {
            _ = try await stateStore.deleteRule(index: index)
        } catch {
            await MainActor.run {
                self.errorMessage = error.localizedDescription
            }
        }
    }
}
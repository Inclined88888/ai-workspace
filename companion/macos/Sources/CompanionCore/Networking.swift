import Foundation

// MARK: - Companion Config Discovery

public actor CompanionConfigStore {
    public static let shared = CompanionConfigStore()

    private let fileURL: URL
    private var cachedConfig: CompanionConfig?
    private var lastModificationDate: Date?

    init(fileURL: URL? = nil) {
        let path = fileURL?.path ?? ProcessInfo.processInfo.environment["ROOKS_COMPANION_FILE"] ?? (NSHomeDirectory() + "/.rooks/companion.json")
        self.fileURL = fileURL ?? URL(fileURLWithPath: path)
    }

    public func loadConfig(forceReload: Bool = false) async throws -> CompanionConfig {
        let attrs = try FileManager.default.attributesOfItem(atPath: fileURL.path)
        let modDate = attrs[.modificationDate] as? Date

        if !forceReload,
           let cached = cachedConfig,
           let lastMod = lastModificationDate,
           let modDate = modDate,
           modDate <= lastMod {
            return cached
        }

        let data = try Data(contentsOf: fileURL)
        let config = try JSONDecoder().decode(CompanionConfig.self, from: data)

        cachedConfig = config
        lastModificationDate = modDate
        return config
    }

    public func invalidateCache() {
        cachedConfig = nil
        lastModificationDate = nil
    }

    // Test helper to allow calling from non-actor context
    public func invalidateCacheForTesting() {
        invalidateCache()
    }

    public var configURL: URL { fileURL }
}

// MARK: - HTTP Client

public struct CompanionHeaders {
    public static let workbench = "X-Workbench"
    public static let workbenchValue = "1"
    public static let authorization = "Authorization"
}

public final class CompanionAPIClient: @unchecked Sendable {
    private let session: URLSession
    private let baseURL: String
    private let token: String

    public init(baseURL: String, token: String, session: URLSession = .shared) {
        self.baseURL = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        self.token = token
        self.session = session
    }

    private func makeRequest(
        path: String,
        method: String = "GET",
        body: Data? = nil,
        headers: [String: String] = [:]
    ) -> URLRequest {
        let url = URL(string: "\(baseURL)\(path)")!
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: CompanionHeaders.authorization)
        request.setValue(CompanionHeaders.workbenchValue, forHTTPHeaderField: CompanionHeaders.workbench)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }
        request.httpBody = body
        return request
    }

    public func getState() async throws -> CompanionState {
        let request = makeRequest(path: "/api/companion/state")
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try JSONDecoder().decode(CompanionState.self, from: data)
    }

    public func postEvent(_ event: EventIn) async throws -> Agent {
        let body = try JSONEncoder().encode(event)
        let request = makeRequest(path: "/api/companion/events", method: "POST", body: body)
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try JSONDecoder().decode(Agent.self, from: data)
    }

    public func postRequest(_ requestIn: RequestIn) async throws -> CompanionRequest {
        let body = try JSONEncoder().encode(requestIn)
        let request = makeRequest(path: "/api/companion/requests", method: "POST", body: body)
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try JSONDecoder().decode(CompanionRequest.self, from: data)
    }

    public func waitForRequest(id: String, timeout: TimeInterval = 55) async throws -> CompanionRequest {
        let path = "/api/companion/requests/\(id)/wait?timeout=\(Int(timeout))"
        let request = makeRequest(path: path)
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try JSONDecoder().decode(CompanionRequest.self, from: data)
    }

    public func decideRequest(id: String, decision: Decision, answer: String? = nil) async throws -> CompanionRequest {
        let decideIn = DecideIn(decision: decision, answer: answer)
        let body = try JSONEncoder().encode(decideIn)
        let request = makeRequest(path: "/api/companion/requests/\(id)/decide", method: "POST", body: body)
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try JSONDecoder().decode(CompanionRequest.self, from: data)
    }

    public func deleteRule(index: Int) async throws -> [AlwaysRule] {
        let request = makeRequest(path: "/api/companion/rules/\(index)", method: "DELETE")
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try JSONDecoder().decode([AlwaysRule].self, from: data)
    }

    public func dropFile(name: String, question: String, data: Data) async throws -> DropResponse {
        var components = URLComponents(string: "\(baseURL)/api/companion/drop")!
        components.queryItems = [
            URLQueryItem(name: "name", value: name),
            URLQueryItem(name: "question", value: question)
        ]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: CompanionHeaders.authorization)
        request.setValue(CompanionHeaders.workbenchValue, forHTTPHeaderField: CompanionHeaders.workbench)
        request.httpBody = data
        let (responseData, response) = try await session.data(for: request)
        try validateResponse(response, data: responseData)
        return try JSONDecoder().decode(DropResponse.self, from: responseData)
    }

    private func validateResponse(_ response: URLResponse, data: Data) throws {
        guard let httpResponse = response as? HTTPURLResponse else {
            throw CompanionError.invalidResponse
        }
        switch httpResponse.statusCode {
        case 200..<300:
            return
        case 401:
            throw CompanionError.unauthorized
        case 404:
            throw CompanionError.notFound
        case 409:
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String {
                throw CompanionError.conflict(detail)
            }
            throw CompanionError.conflict("Request already answered")
        case 413:
            throw CompanionError.payloadTooLarge
        case 422:
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String {
                throw CompanionError.unprocessableEntity(detail)
            }
            throw CompanionError.unprocessableEntity("Invalid request")
        default:
            let message = String(data: data, encoding: .utf8) ?? "Unknown error"
            throw CompanionError.serverError(httpResponse.statusCode, message)
        }
    }
}

public enum CompanionError: Error, LocalizedError, Sendable {
    case invalidResponse
    case unauthorized
    case notFound
    case conflict(String)
    case payloadTooLarge
    case unprocessableEntity(String)
    case serverError(Int, String)
    case configNotFound
    case configInvalid
    case sseParseError(String)
    case networkError(Error)

    public var errorDescription: String? {
        switch self {
        case .invalidResponse: return "Invalid server response"
        case .unauthorized: return "Authentication failed — the server restarted or token changed"
        case .notFound: return "Resource not found"
        case .conflict(let msg): return msg
        case .payloadTooLarge: return "File too large (max 100 MB)"
        case .unprocessableEntity(let msg): return msg
        case .serverError(let code, let msg): return "Server error (\(code)): \(msg)"
        case .configNotFound: return "Rooks isn't running. Start it from the menu or run `ai-workspace`."
        case .configInvalid: return "Invalid companion configuration"
        case .sseParseError(let msg): return "SSE parse error: \(msg)"
        case .networkError(let err): return "Network error: \(err.localizedDescription)"
        }
    }
}

// MARK: - SSE Parser

public struct SSEEvent: Sendable {
    public let event: String?
    public let data: String
    public let id: String?
    public let retry: Int?

    public var isPing: Bool { data == "ping" || data.hasPrefix(":") }
    public var isData: Bool { !isPing }
}

public final class SSEParser: @unchecked Sendable {
    private var buffer = ""

    public init() {}

    public func parse(_ chunk: String) -> [SSEEvent] {
        buffer += chunk
        var events: [SSEEvent] = []

        while let range = buffer.range(of: "\n\n") {
            let eventText = String(buffer[..<range.lowerBound])
            buffer = String(buffer[range.upperBound...])
            if let event = parseEvent(eventText) {
                events.append(event)
            }
        }

        return events
    }

    public func flush() -> SSEEvent? {
        guard !buffer.isEmpty else { return nil }
        defer { buffer = "" }
        return parseEvent(buffer)
    }

    private func parseEvent(_ text: String) -> SSEEvent? {
        var event: String?
        var dataLines: [String] = []
        var id: String?
        var retry: Int?

        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix("event:") {
                event = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                dataLines.append(String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces))
            } else if line.hasPrefix("id:") {
                id = String(line.dropFirst(3)).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("retry:") {
                retry = Int(String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces))
            } else if line.hasPrefix(":") {
                // Comment/ping line
                return SSEEvent(event: nil, data: String(line), id: nil, retry: nil)
            }
        }

        guard !dataLines.isEmpty else { return nil }
        return SSEEvent(event: event, data: dataLines.joined(separator: "\n"), id: id, retry: retry)
    }
}

// MARK: - SSE Client (using async bytes instead of delegate)

public final class SSEClient: @unchecked Sendable {
    private let request: URLRequest
    private var task: Task<Void, Never>?
    private let parser = SSEParser()
    private let continuation: AsyncStream<SSEEvent>.Continuation

    public let events: AsyncStream<SSEEvent>

    public init(url: URL, token: String) {
        var req = URLRequest(url: url)
        req.setValue("Bearer \(token)", forHTTPHeaderField: CompanionHeaders.authorization)
        req.setValue(CompanionHeaders.workbenchValue, forHTTPHeaderField: CompanionHeaders.workbench)
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        req.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        self.request = req

        var cont: AsyncStream<SSEEvent>.Continuation!
        self.events = AsyncStream { continuation in
            cont = continuation
        }
        self.continuation = cont
    }

    public func connect() {
        task = Task { [request, parser, continuation] in
            do {
                let (bytes, response) = try await URLSession.shared.bytes(for: request)

                guard let httpResponse = response as? HTTPURLResponse else {
                    continuation.finish()
                    return
                }

                if httpResponse.statusCode == 401 {
                    continuation.finish()
                    return
                }

                if !(200..<300).contains(httpResponse.statusCode) {
                    continuation.finish()
                    return
                }

                for try await line in bytes.lines {
                    if Task.isCancelled { break }
                    let events = parser.parse(line + "\n")
                    for event in events {
                        continuation.yield(event)
                    }
                }

                // Flush any remaining
                if let finalEvent = parser.flush() {
                    continuation.yield(finalEvent)
                }

                continuation.finish()
            } catch {
                if !Task.isCancelled {
                    continuation.finish()
                }
            }
        }
    }

    public func disconnect() {
        task?.cancel()
        task = nil
        continuation.finish()
    }

    deinit {
        disconnect()
    }
}

// MARK: - State Reducer

public actor StateStore {
    public typealias Listener = @Sendable (CompanionState) -> Void

    public static let shared = StateStore()

    private var currentState: CompanionState?
    private var listeners: [UUID: Listener] = [:]
    private var sseClient: SSEClient?
    private var apiClient: CompanionAPIClient?
    private let configStore: CompanionConfigStore
    private var reconnectTask: Task<Void, Never>?
    private var _isConnected = false

    public init(configStore: CompanionConfigStore = .shared) {
        self.configStore = configStore
    }

    public var state: CompanionState? { currentState }

    public var isConnected: Bool { _isConnected }

    public func addListener(_ listener: @escaping Listener) -> UUID {
        let id = UUID()
        listeners[id] = listener
        if let state = currentState {
            listener(state)
        }
        return id
    }

    public func removeListener(_ id: UUID) {
        listeners.removeValue(forKey: id)
    }

    private func notifyListeners(_ state: CompanionState) {
        for listener in listeners.values {
            listener(state)
        }
    }

    public func connect() async {
        await reconnect()
    }

    public func disconnect() {
        reconnectTask?.cancel()
        reconnectTask = nil
        sseClient?.disconnect()
        sseClient = nil
        apiClient = nil
        _isConnected = false
    }

    private func reconnect() async {
        reconnectTask?.cancel()
        reconnectTask = Task { [weak self] in
            guard let self = self else { return }
            var backoff: UInt64 = 1_000_000_000 // 1 second
            let maxBackoff: UInt64 = 30_000_000_000 // 30 seconds

            while !Task.isCancelled {
                do {
                    try await self.establishConnection()
                    await self.setConnected(true)
                    backoff = 1_000_000_000 // Reset backoff on success
                    try await Task.sleep(nanoseconds: maxBackoff) // Keep connection alive
                } catch {
                    await self.setConnected(false)
                    if Task.isCancelled { break }
                    // Exponential backoff with jitter
                    let jitter = UInt64.random(in: 0..<backoff/2)
                    try? await Task.sleep(nanoseconds: backoff + jitter)
                    backoff = min(backoff * 2, maxBackoff)
                }
            }
        }
    }

    private func setConnected(_ value: Bool) {
        _isConnected = value
    }

    private func establishConnection() async throws {
        let config = try await configStore.loadConfig(forceReload: true)
        let apiClient = CompanionAPIClient(baseURL: config.url, token: config.token)
        self.apiClient = apiClient

        // Fetch initial state
        let initialState = try await apiClient.getState()
        self.currentState = initialState
        notifyListeners(initialState)

        // Connect to SSE stream
        guard let sseURL = URL(string: "\(config.url)/api/companion/stream") else {
            throw CompanionError.configInvalid
        }
        let sseClient = SSEClient(url: sseURL, token: config.token)
        self.sseClient = sseClient
        sseClient.connect()

        // Process SSE events
        for await event in sseClient.events {
            if event.isPing { continue }
            if let data = event.data.data(using: .utf8),
               let state = try? JSONDecoder().decode(CompanionState.self, from: data) {
                self.currentState = state
                notifyListeners(state)
            }
        }
    }

    // MARK: - Actions

    public func postEvent(_ event: EventIn) async throws -> Agent {
        guard let client = apiClient else { throw CompanionError.configNotFound }
        return try await client.postEvent(event)
    }

    public func postRequest(_ request: RequestIn) async throws -> CompanionRequest {
        guard let client = apiClient else { throw CompanionError.configNotFound }
        return try await client.postRequest(request)
    }

    public func waitForRequest(id: String, timeout: TimeInterval = 55) async throws -> CompanionRequest {
        guard let client = apiClient else { throw CompanionError.configNotFound }
        return try await client.waitForRequest(id: id, timeout: timeout)
    }

    public func decideRequest(id: String, decision: Decision, answer: String? = nil) async throws -> CompanionRequest {
        guard let client = apiClient else { throw CompanionError.configNotFound }
        return try await client.decideRequest(id: id, decision: decision, answer: answer)
    }

    public func deleteRule(index: Int) async throws -> [AlwaysRule] {
        guard let client = apiClient else { throw CompanionError.configNotFound }
        return try await client.deleteRule(index: index)
    }

    public func dropFile(name: String, question: String, data: Data) async throws -> DropResponse {
        guard let client = apiClient else { throw CompanionError.configNotFound }
        return try await client.dropFile(name: name, question: question, data: data)
    }

    public func refreshConfig() async {
        await configStore.invalidateCache()
        await reconnect()
    }
}
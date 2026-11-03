import Foundation

// MARK: - State Models

public struct CompanionState: Codable, Sendable {
    public let version: Int
    public let mood: Mood
    public let agents: [Agent]
    public let requests: [CompanionRequest]
    public let usage: Usage
    public let rules: [AlwaysRule]

    public init(
        version: Int,
        mood: Mood,
        agents: [Agent],
        requests: [CompanionRequest],
        usage: Usage,
        rules: [AlwaysRule]
    ) {
        self.version = version
        self.mood = mood
        self.agents = agents
        self.requests = requests
        self.usage = usage
        self.rules = rules
    }
}

public enum Mood: String, Codable, Sendable {
    case idle
    case working
    case waiting
    case done
}

public struct Agent: Codable, Sendable, Identifiable {
    public let key: String
    public let tool: String
    public let session: String
    public let name: String
    public let folder: String
    public let status: AgentStatus
    public let steps: [Step]
    public let usage: AgentUsage?
    public let updatedAt: Double
    public let startedAt: Double?

    public var id: String { key }

    public init(
        key: String,
        tool: String,
        session: String,
        name: String,
        folder: String,
        status: AgentStatus,
        steps: [Step],
        usage: AgentUsage?,
        updatedAt: Double,
        startedAt: Double?
    ) {
        self.key = key
        self.tool = tool
        self.session = session
        self.name = name
        self.folder = folder
        self.status = status
        self.steps = steps
        self.usage = usage
        self.updatedAt = updatedAt
        self.startedAt = startedAt
    }

    enum CodingKeys: String, CodingKey {
        case key, tool, session, name, folder, status, steps, usage
        case updatedAt = "updated_at"
        case startedAt = "started_at"
    }
}

public enum AgentStatus: String, Codable, Sendable {
    case idle
    case working
    case thinking
    case waiting
    case done
    case error
}

public struct Step: Codable, Sendable, Identifiable {
    public let t: Double
    public let kind: StepKind
    public let text: String

    public var id: Double { t }

    public init(t: Double, kind: StepKind, text: String) {
        self.t = t
        self.kind = kind
        self.text = text
    }
}

public enum StepKind: String, Codable, Sendable {
    case read
    case edit
    case run
    case search
    case web
    case think
    case other
    case done
    case error
}

public struct AgentUsage: Codable, Sendable {
    public let label: String
    public let used: Double
    public let resetsAt: Double?

    public init(label: String, used: Double, resetsAt: Double?) {
        self.label = label
        self.used = used
        self.resetsAt = resetsAt
    }

    enum CodingKeys: String, CodingKey {
        case label, used
        case resetsAt = "resets_at"
    }
}

public struct CompanionRequest: Codable, Sendable, Identifiable {
    public let id: String
    public let tool: String
    public let session: String
    public let agent: String
    public let agentKey: String
    public let kind: RequestKind
    public let title: String
    public let detail: String
    public let action: String
    public let target: String
    public let options: [String]
    public let multi: Bool
    public let status: RequestStatus
    public let answer: String?
    public let decidedBy: String?
    public let decidedAt: Double?
    public let createdAt: Double
    public let always: Bool

    public init(
        id: String,
        tool: String,
        session: String,
        agent: String,
        agentKey: String,
        kind: RequestKind,
        title: String,
        detail: String,
        action: String,
        target: String,
        options: [String],
        multi: Bool,
        status: RequestStatus,
        answer: String?,
        decidedBy: String?,
        decidedAt: Double?,
        createdAt: Double,
        always: Bool
    ) {
        self.id = id
        self.tool = tool
        self.session = session
        self.agent = agent
        self.agentKey = agentKey
        self.kind = kind
        self.title = title
        self.detail = detail
        self.action = action
        self.target = target
        self.options = options
        self.multi = multi
        self.status = status
        self.answer = answer
        self.decidedBy = decidedBy
        self.decidedAt = decidedAt
        self.createdAt = createdAt
        self.always = always
    }

    enum CodingKeys: String, CodingKey {
        case id, tool, session, agent, kind, title, detail, action, target, options, multi, status, answer, always
        case agentKey = "agent_key"
        case decidedBy = "decided_by"
        case decidedAt = "decided_at"
        case createdAt = "created_at"
    }
}

public enum RequestKind: String, Codable, Sendable {
    case permission
    case question
}

public enum RequestStatus: String, Codable, Sendable {
    case pending
    case allow
    case deny
    case answered
}

public struct Usage: Codable, Sendable {
    public let rooksMonth: RooksMonthUsage?
    public let agents: [AgentUsageSummary]

    public init(rooksMonth: RooksMonthUsage?, agents: [AgentUsageSummary]) {
        self.rooksMonth = rooksMonth
        self.agents = agents
    }

    enum CodingKeys: String, CodingKey {
        case rooksMonth = "rooks_month"
        case agents
    }
}

public struct RooksMonthUsage: Codable, Sendable {
    public let cents: Int
    public let tokens: Int

    public init(cents: Int, tokens: Int) {
        self.cents = cents
        self.tokens = tokens
    }
}

public struct AgentUsageSummary: Codable, Sendable, Identifiable {
    public let name: String
    public let label: String
    public let used: Double
    public let resetsAt: Double?

    public var id: String { name }

    public init(name: String, label: String, used: Double, resetsAt: Double?) {
        self.name = name
        self.label = label
        self.used = used
        self.resetsAt = resetsAt
    }

    enum CodingKeys: String, CodingKey {
        case name, label, used
        case resetsAt = "resets_at"
    }
}

public struct AlwaysRule: Codable, Sendable, Identifiable {
    public let tool: String
    public let action: String
    public let pattern: String
    public let createdAt: Double

    public var id: String { "\(tool):\(action):\(pattern)" }

    public init(tool: String, action: String, pattern: String, createdAt: Double) {
        self.tool = tool
        self.action = action
        self.pattern = pattern
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case tool, action, pattern
        case createdAt = "created_at"
    }
}

// MARK: - Event Models (for POST /api/companion/events)

public struct EventIn: Codable, Sendable {
    public let tool: String
    public let session: String
    public let name: String
    public let folder: String
    public let event: EventType
    public let text: String
    public let kind: String
    public let usage: AgentUsage?

    public init(
        tool: String,
        session: String = "",
        name: String = "",
        folder: String = "",
        event: EventType,
        text: String = "",
        kind: String = "",
        usage: AgentUsage? = nil
    ) {
        self.tool = tool
        self.session = session
        self.name = name
        self.folder = folder
        self.event = event
        self.text = text
        self.kind = kind
        self.usage = usage
    }
}

public enum EventType: String, Codable, Sendable {
    case start
    case step
    case thinking
    case idle
    case done
    case error
    case usage
}

// MARK: - Request Models (for POST /api/companion/requests)

public struct RequestIn: Codable, Sendable {
    public let tool: String
    public let session: String
    public let name: String
    public let kind: RequestKind
    public let title: String
    public let detail: String
    public let action: String
    public let target: String
    public let options: [String]
    public let multi: Bool

    public init(
        tool: String,
        session: String = "",
        name: String = "",
        kind: RequestKind,
        title: String,
        detail: String = "",
        action: String,
        target: String = "",
        options: [String] = [],
        multi: Bool = false
    ) {
        self.tool = tool
        self.session = session
        self.name = name
        self.kind = kind
        self.title = title
        self.detail = detail
        self.action = action
        self.target = target
        self.options = options
        self.multi = multi
    }
}

// MARK: - Decide Models (for POST /api/companion/requests/{id}/decide)

public struct DecideIn: Codable, Sendable {
    public let decision: Decision
    public let answer: String?

    public init(decision: Decision, answer: String? = nil) {
        self.decision = decision
        self.answer = answer
    }
}

public enum Decision: String, Codable, Sendable {
    case allow
    case deny
    case always
    case answer
}

// MARK: - Drop Response Model

public struct DropResponse: Codable, Sendable {
    public let file: DroppedFile
    public let prompt: String
    public let open: String

    public init(file: DroppedFile, prompt: String, open: String) {
        self.file = file
        self.prompt = prompt
        self.open = open
    }
}

public struct DroppedFile: Codable, Sendable {
    public let id: String?
    public let name: String

    public init(id: String?, name: String) {
        self.id = id
        self.name = name
    }
}

// MARK: - Companion.json Discovery

public struct CompanionConfig: Codable, Sendable {
    public let url: String
    public let token: String
    public let pid: Int?

    public init(url: String, token: String, pid: Int? = nil) {
        self.url = url
        self.token = token
        self.pid = pid
    }
}
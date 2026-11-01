import Foundation
import SwiftUI

// MARK: - Mood to Mascot Mapping

public enum MascotState: Sendable {
    case idle
    case working
    case waiting
    case done

    public init(mood: Mood) {
        switch mood {
        case .idle: self = .idle
        case .working: self = .working
        case .waiting: self = .waiting
        case .done: self = .done
        }
    }
}

public extension MascotState {
    /// The system symbol name for the mascot in collapsed pill
    var symbolName: String {
        switch self {
        case .idle: return "circle.fill"
        case .working: return "gearshape.2.fill"
        case .waiting: return "exclamationmark.circle.fill"
        case .done: return "checkmark.circle.fill"
        }
    }

    /// The attention color for the mascot (used for pulsing/bouncing)
    var attentionColor: Color {
        switch self {
        case .idle: return .secondary
        case .working: return .blue
        case .waiting: return .orange
        case .done: return .green
        }
    }

    /// Whether the mascot should animate
    var isAnimated: Bool {
        switch self {
        case .idle: return false
        case .working: return true
        case .waiting: return true
        case .done: return false
        }
    }

    /// Animation type for the mascot
    var animationType: MascotAnimation {
        switch self {
        case .idle: return .none
        case .working: return .rotate
        case .waiting: return .bounce
        case .done: return .pulse
        }
    }
}

public enum MascotAnimation: Sendable {
    case none
    case rotate
    case bounce
    case pulse
}

// MARK: - Status Helpers

public extension AgentStatus {
    var displayName: String {
        switch self {
        case .idle: return "Idle"
        case .working: return "Working"
        case .thinking: return "Thinking"
        case .waiting: return "Waiting"
        case .done: return "Done"
        case .error: return "Error"
        }
    }

    var color: Color {
        switch self {
        case .idle: return .secondary
        case .working: return .blue
        case .thinking: return .purple
        case .waiting: return .orange
        case .done: return .green
        case .error: return .red
        }
    }

    var symbolName: String {
        switch self {
        case .idle: return "circle"
        case .working: return "circle.fill"
        case .thinking: return "brain.head.profile"
        case .waiting: return "clock.fill"
        case .done: return "checkmark.circle.fill"
        case .error: return "exclamationmark.triangle.fill"
        }
    }
}

public extension RequestKind {
    var displayName: String {
        switch self {
        case .permission: return "Permission"
        case .question: return "Question"
        }
    }
}

public extension RequestStatus {
    var displayName: String {
        switch self {
        case .pending: return "Pending"
        case .allow: return "Allowed"
        case .deny: return "Denied"
        case .answered: return "Answered"
        }
    }
}

// MARK: - Formatting Helpers

public extension Double {
    /// Format timestamp as relative time (e.g., "2m ago", "just now")
    var relativeTimeString: String {
        let now = Date().timeIntervalSince1970
        let diff = now - self
        if diff < 60 { return "just now" }
        if diff < 3600 { return "\(Int(diff/60))m ago" }
        if diff < 86400 { return "\(Int(diff/3600))h ago" }
        return "\(Int(diff/86400))d ago"
    }
}

public extension Int {
    /// Format cents as dollar string (e.g., 1234 -> "$12.34")
    var dollarsString: String {
        let dollars = self / 100
        let cents = abs(self % 100)
        return String(format: "$%d.%02d", dollars, cents)
    }
}

public extension AgentUsageSummary {
    /// Progress value 0..1 for usage bar
    var progress: Double {
        min(max(used, 0), 1)
    }

    /// Formatted usage string (e.g., "42% of 5-hour")
    var formattedUsage: String {
        "\(Int(used * 100))% of \(label)"
    }
}

public extension RooksMonthUsage {
    var formattedCost: String { cents.dollarsString }
}

// MARK: - Step Kind Helpers

public extension StepKind {
    var symbolName: String {
        switch self {
        case .read: return "doc.text"
        case .edit: return "pencil"
        case .run: return "play.fill"
        case .search: return "magnifyingglass"
        case .web: return "globe"
        case .think: return "brain.head.profile"
        case .other: return "circle"
        case .done: return "checkmark"
        case .error: return "xmark"
        }
    }

    var color: Color {
        switch self {
        case .read: return .blue
        case .edit: return .green
        case .run: return .orange
        case .search: return .purple
        case .web: return .cyan
        case .think: return .indigo
        case .other: return .gray
        case .done: return .green
        case .error: return .red
        }
    }
}
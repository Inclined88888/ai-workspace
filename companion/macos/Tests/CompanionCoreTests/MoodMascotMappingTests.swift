import Foundation
import Testing
import SwiftUI
@testable import CompanionCore

@Suite("Mood to Mascot Mapping Tests")
struct MoodMascotMappingTests {

    @Test("Idle mood maps to idle mascot")
    func testIdleMoodMapsToIdleMascot() {
        let mascot = MascotState(mood: .idle)
        #expect(mascot == .idle)
    }

    @Test("Working mood maps to working mascot")
    func testWorkingMoodMapsToWorkingMascot() {
        let mascot = MascotState(mood: .working)
        #expect(mascot == .working)
    }

    @Test("Waiting mood maps to waiting mascot")
    func testWaitingMoodMapsToWaitingMascot() {
        let mascot = MascotState(mood: .waiting)
        #expect(mascot == .waiting)
    }

    @Test("Done mood maps to done mascot")
    func testDoneMoodMapsToDoneMascot() {
        let mascot = MascotState(mood: .done)
        #expect(mascot == .done)
    }

    @Test("Mascot symbol names")
    func testMascotSymbolNames() {
        #expect(MascotState.idle.symbolName == "circle.fill")
        #expect(MascotState.working.symbolName == "gearshape.2.fill")
        #expect(MascotState.waiting.symbolName == "exclamationmark.circle.fill")
        #expect(MascotState.done.symbolName == "checkmark.circle.fill")
    }

    @Test("Mascot animation flags")
    func testMascotAnimationFlags() {
        #expect(MascotState.idle.isAnimated == false)
        #expect(MascotState.working.isAnimated == true)
        #expect(MascotState.waiting.isAnimated == true)
        #expect(MascotState.done.isAnimated == false)
    }

    @Test("Mascot animation types")
    func testMascotAnimationTypes() {
        #expect(MascotState.idle.animationType == .none)
        #expect(MascotState.working.animationType == .rotate)
        #expect(MascotState.waiting.animationType == .bounce)
        #expect(MascotState.done.animationType == .pulse)
    }

    @Test("Agent status display names")
    func testAgentStatusDisplayNames() {
        #expect(AgentStatus.idle.displayName == "Idle")
        #expect(AgentStatus.working.displayName == "Working")
        #expect(AgentStatus.thinking.displayName == "Thinking")
        #expect(AgentStatus.waiting.displayName == "Waiting")
        #expect(AgentStatus.done.displayName == "Done")
        #expect(AgentStatus.error.displayName == "Error")
    }

    @Test("Agent status symbol names")
    func testAgentStatusSymbolNames() {
        #expect(AgentStatus.idle.symbolName == "circle")
        #expect(AgentStatus.working.symbolName == "circle.fill")
        #expect(AgentStatus.thinking.symbolName == "brain.head.profile")
        #expect(AgentStatus.waiting.symbolName == "clock.fill")
        #expect(AgentStatus.done.symbolName == "checkmark.circle.fill")
        #expect(AgentStatus.error.symbolName == "exclamationmark.triangle.fill")
    }

    @Test("Request kind display names")
    func testRequestKindDisplayNames() {
        #expect(RequestKind.permission.displayName == "Permission")
        #expect(RequestKind.question.displayName == "Question")
    }

    @Test("Request status display names")
    func testRequestStatusDisplayNames() {
        #expect(RequestStatus.pending.displayName == "Pending")
        #expect(RequestStatus.allow.displayName == "Allowed")
        #expect(RequestStatus.deny.displayName == "Denied")
        #expect(RequestStatus.answered.displayName == "Answered")
    }

    @Test("Relative time string formatting")
    func testRelativeTimeString() {
        let now = Date().timeIntervalSince1970
        #expect((now - 30).relativeTimeString == "just now")
        #expect((now - 90).relativeTimeString == "1m ago")
        #expect((now - 3660).relativeTimeString == "1h ago")
        #expect((now - 90000).relativeTimeString == "1d ago")
    }

    @Test("Dollars string formatting")
    func testDollarsString() {
        #expect(1234.dollarsString == "$12.34")
        #expect(0.dollarsString == "$0.00")
        #expect(100.dollarsString == "$1.00")
        #expect(99.dollarsString == "$0.99")
        #expect((-50).dollarsString == "$0.50")
    }

    @Test("Agent usage progress clamping")
    func testAgentUsageProgress() {
        let usage1 = AgentUsageSummary(name: "Test", label: "5-hour", used: 0.0, resetsAt: nil)
        #expect(usage1.progress == 0.0)

        let usage2 = AgentUsageSummary(name: "Test", label: "5-hour", used: 0.5, resetsAt: nil)
        #expect(usage2.progress == 0.5)

        let usage3 = AgentUsageSummary(name: "Test", label: "5-hour", used: 1.0, resetsAt: nil)
        #expect(usage3.progress == 1.0)

        let usage4 = AgentUsageSummary(name: "Test", label: "5-hour", used: 1.5, resetsAt: nil)
        #expect(usage4.progress == 1.0)

        let usage5 = AgentUsageSummary(name: "Test", label: "5-hour", used: -0.5, resetsAt: nil)
        #expect(usage5.progress == 0.0)
    }

    @Test("Agent usage formatted string")
    func testAgentUsageFormatted() {
        let usage = AgentUsageSummary(name: "Test", label: "5-hour", used: 0.42, resetsAt: nil)
        #expect(usage.formattedUsage == "42% of 5-hour")
    }

    @Test("Rooks month formatted cost")
    func testRooksMonthFormattedCost() {
        let usage = RooksMonthUsage(cents: 1234, tokens: 50000)
        #expect(usage.formattedCost == "$12.34")
    }

    @Test("Step kind symbol names")
    func testStepKindSymbolNames() {
        #expect(StepKind.read.symbolName == "doc.text")
        #expect(StepKind.edit.symbolName == "pencil")
        #expect(StepKind.run.symbolName == "play.fill")
        #expect(StepKind.search.symbolName == "magnifyingglass")
        #expect(StepKind.web.symbolName == "globe")
        #expect(StepKind.think.symbolName == "brain.head.profile")
        #expect(StepKind.other.symbolName == "circle")
        #expect(StepKind.done.symbolName == "checkmark")
        #expect(StepKind.error.symbolName == "xmark")
    }
}
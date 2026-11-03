import Foundation
import Testing
@testable import CompanionCore

@Suite("Request Building Tests")
struct RequestBuildingTests {

    @Test("Decide request allow")
    func testDecideRequestAllow() throws {
        let decide = DecideIn(decision: .allow)
        let data = try JSONEncoder().encode(decide)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["decision"] as? String == "allow")
        #expect(json["answer"] == nil)
    }

    @Test("Decide request deny")
    func testDecideRequestDeny() throws {
        let decide = DecideIn(decision: .deny)
        let data = try JSONEncoder().encode(decide)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["decision"] as? String == "deny")
    }

    @Test("Decide request always")
    func testDecideRequestAlways() throws {
        let decide = DecideIn(decision: .always)
        let data = try JSONEncoder().encode(decide)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["decision"] as? String == "always")
    }

    @Test("Decide request answer")
    func testDecideRequestAnswer() throws {
        let decide = DecideIn(decision: .answer, answer: "my custom answer")
        let data = try JSONEncoder().encode(decide)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["decision"] as? String == "answer")
        #expect(json["answer"] as? String == "my custom answer")
    }

    @Test("Decide request answer with empty string")
    func testDecideRequestAnswerWithEmptyString() throws {
        let decide = DecideIn(decision: .answer, answer: "")
        let data = try JSONEncoder().encode(decide)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["decision"] as? String == "answer")
        #expect(json["answer"] as? String == "")
    }

    @Test("Drop request model")
    func testDropRequestModel() throws {
        let file = DroppedFile(id: "file-123", name: "document.pdf")
        let response = DropResponse(file: file, prompt: "What is this?", open: "/chat?file=file-123&prompt=What+is+this%3F")

        let data = try JSONEncoder().encode(response)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect((json["file"] as? [String: Any])?["id"] as? String == "file-123")
        #expect((json["file"] as? [String: Any])?["name"] as? String == "document.pdf")
        #expect(json["prompt"] as? String == "What is this?")
        #expect(json["open"] as? String == "/chat?file=file-123&prompt=What+is+this%3F")
    }

    @Test("Companion headers constants")
    func testCompanionHeadersConstants() {
        #expect(CompanionHeaders.workbench == "X-Workbench")
        #expect(CompanionHeaders.workbenchValue == "1")
        #expect(CompanionHeaders.authorization == "Authorization")
    }

    @Test("Build permission request")
    func testBuildPermissionRequest() throws {
        let request = RequestIn(
            tool: "claude-code",
            session: "s1",
            name: "Claude Code",
            kind: .permission,
            title: "Run npm test",
            detail: "Execute the test suite",
            action: "Bash",
            target: "npm test",
            options: [],
            multi: false
        )

        let data = try JSONEncoder().encode(request)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["tool"] as? String == "claude-code")
        #expect(json["session"] as? String == "s1")
        #expect(json["kind"] as? String == "permission")
        #expect(json["title"] as? String == "Run npm test")
        #expect(json["action"] as? String == "Bash")
        #expect(json["target"] as? String == "npm test")
        #expect((json["options"] as? [String])?.count == 0)
        #expect(json["multi"] as? Bool == false)
    }

    @Test("Build question request")
    func testBuildQuestionRequest() throws {
        let request = RequestIn(
            tool: "gemini-cli",
            session: "s2",
            kind: .question,
            title: "Which file to refactor?",
            detail: "Pick one",
            action: "",
            target: "",
            options: ["api.py", "db.py", "utils.py"],
            multi: true
        )

        let data = try JSONEncoder().encode(request)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["kind"] as? String == "question")
        #expect((json["options"] as? [String])?.count == 3)
        #expect(json["multi"] as? Bool == true)
    }

    @Test("Build start event")
    func testBuildStartEvent() throws {
        let event = EventIn(
            tool: "claude-code",
            session: "s1",
            name: "Claude Code",
            folder: "/project",
            event: .start,
            text: "Starting task",
            kind: "other"
        )

        let data = try JSONEncoder().encode(event)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["event"] as? String == "start")
        #expect(json["text"] as? String == "Starting task")
    }

    @Test("Build step event")
    func testBuildStepEvent() throws {
        let event = EventIn(
            tool: "codex",
            session: "s2",
            event: .step,
            text: "Edited main.swift",
            kind: "edit"
        )

        let data = try JSONEncoder().encode(event)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["event"] as? String == "step")
        #expect(json["kind"] as? String == "edit")
    }

    @Test("Build usage event")
    func testBuildUsageEvent() throws {
        let usage = AgentUsage(label: "5-hour", used: 0.75, resetsAt: 1793500000)
        let event = EventIn(
            tool: "claude-code",
            session: "s1",
            event: .usage,
            usage: usage
        )

        let data = try JSONEncoder().encode(event)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["event"] as? String == "usage")
        let usageJson = json["usage"] as! [String: Any]
        #expect(usageJson["label"] as? String == "5-hour")
        #expect(usageJson["used"] as? Double == 0.75)
        #expect(usageJson["resets_at"] as? Double == 1793500000)
    }

    @Test("Build thinking event")
    func testBuildThinkingEvent() throws {
        let event = EventIn(
            tool: "cursor",
            session: "s3",
            event: .thinking,
            text: "Analyzing codebase",
            kind: "think"
        )

        let data = try JSONEncoder().encode(event)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        #expect(json["event"] as? String == "thinking")
        #expect(json["kind"] as? String == "think")
    }
}
import Foundation
import Testing
@testable import CompanionCore

@Suite("Models Tests")
struct ModelsTests {

    @Test("Decode CompanionState with all fields")
    func testDecodeCompanionState() throws {
        let json = """
        {
            "version": 42,
            "mood": "working",
            "agents": [
                {
                    "key": "claude-code:s1",
                    "tool": "claude-code",
                    "session": "s1",
                    "name": "Claude Code",
                    "folder": "/tmp/proj",
                    "status": "working",
                    "steps": [
                        {"t": 1234567890.0, "kind": "read", "text": "Read app.py"},
                        {"t": 1234567891.0, "kind": "edit", "text": "Edit main.swift"}
                    ],
                    "usage": {"label": "5-hour", "used": 0.42, "resets_at": 1793500000},
                    "updated_at": 1234567892.0,
                    "started_at": 1234567800.0
                }
            ],
            "requests": [
                {
                    "id": "abc123",
                    "tool": "claude-code",
                    "session": "s1",
                    "agent": "Claude Code",
                    "agent_key": "claude-code:s1",
                    "kind": "permission",
                    "title": "Run npm test",
                    "detail": "Execute test suite",
                    "action": "Bash",
                    "target": "npm test",
                    "options": [],
                    "multi": false,
                    "status": "pending",
                    "answer": null,
                    "decided_by": "",
                    "decided_at": null,
                    "created_at": 1234567890.0,
                    "always": true
                }
            ],
            "usage": {
                "rooks_month": {"cents": 1234, "tokens": 50000},
                "agents": [{"name": "Claude Code", "label": "5-hour", "used": 0.42, "resets_at": 1793500000}]
            },
            "rules": [
                {"tool": "codex", "action": "Bash", "pattern": "pytest*", "created_at": 1234567000.0}
            ]
        }
        """

        let data = json.data(using: .utf8)!
        let state = try JSONDecoder().decode(CompanionState.self, from: data)

        #expect(state.version == 42)
        #expect(state.mood == .working)
        #expect(state.agents.count == 1)
        #expect(state.agents[0].key == "claude-code:s1")
        #expect(state.agents[0].status == .working)
        #expect(state.agents[0].steps.count == 2)
        #expect(state.agents[0].steps[0].kind == .read)
        #expect(state.agents[0].usage?.label == "5-hour")
        #expect(state.requests.count == 1)
        #expect(state.requests[0].id == "abc123")
        #expect(state.requests[0].kind == .permission)
        #expect(state.requests[0].always == true)
        #expect(state.usage.rooksMonth?.cents == 1234)
        #expect(state.usage.agents.count == 1)
        #expect(state.rules.count == 1)
        #expect(state.rules[0].pattern == "pytest*")
    }

    @Test("Decode state with minimal fields")
    func testDecodeStateWithMissingOptionalFields() throws {
        let json = """
        {
            "version": 1,
            "mood": "idle",
            "agents": [],
            "requests": [],
            "usage": {"rooks_month": null, "agents": []},
            "rules": []
        }
        """

        let data = json.data(using: .utf8)!
        let state = try JSONDecoder().decode(CompanionState.self, from: data)

        #expect(state.version == 1)
        #expect(state.mood == .idle)
        #expect(state.agents.isEmpty)
        #expect(state.requests.isEmpty)
        #expect(state.usage.rooksMonth == nil)
        #expect(state.usage.agents.isEmpty)
        #expect(state.rules.isEmpty)
    }

    @Test("Decode all moods", arguments: ["idle", "working", "waiting", "done"])
    func testDecodeAllMoods(moodString: String) throws {
        let json = """
        {"version": 1, "mood": "\(moodString)", "agents": [], "requests": [],
         "usage": {"rooks_month": null, "agents": []}, "rules": []}
        """
        let data = json.data(using: .utf8)!
        let state = try JSONDecoder().decode(CompanionState.self, from: data)
        #expect(state.mood.rawValue == moodString)
    }

    @Test("Decode all agent statuses", arguments: ["idle", "working", "thinking", "waiting", "done", "error"])
    func testDecodeAllAgentStatuses(statusString: String) throws {
        let json = """
        {"key": "t:s", "tool": "t", "session": "s", "name": "N", "folder": "",
         "status": "\(statusString)", "steps": [], "usage": null,
         "updated_at": 0, "started_at": 0}
        """
        let data = json.data(using: .utf8)!
        let agent = try JSONDecoder().decode(Agent.self, from: data)
        #expect(agent.status.rawValue == statusString)
    }

    @Test("Decode all step kinds", arguments: ["read", "edit", "run", "search", "web", "think", "other", "done", "error"])
    func testDecodeAllStepKinds(kindString: String) throws {
        let json = "{\"t\": 0, \"kind\": \"\(kindString)\", \"text\": \"\"}"
        let data = json.data(using: .utf8)!
        let step = try JSONDecoder().decode(Step.self, from: data)
        #expect(step.kind.rawValue == kindString)
    }

    @Test("Decode permission request")
    func testDecodePermissionRequest() throws {
        let json = """
        {"id": "1", "tool": "t", "session": "s", "agent": "A", "agent_key": "k",
         "kind": "permission", "title": "T", "detail": "", "action": "A", "target": "",
         "options": [], "multi": false, "status": "pending", "answer": null,
         "decided_by": "", "decided_at": null, "created_at": 0, "always": false}
        """
        let data = json.data(using: .utf8)!
        let req = try JSONDecoder().decode(CompanionRequest.self, from: data)
        #expect(req.kind == .permission)
        #expect(req.status == .pending)
    }

    @Test("Decode question request with options")
    func testDecodeQuestionRequest() throws {
        let json = """
        {"id": "2", "tool": "t", "session": "s", "agent": "A", "agent_key": "k",
         "kind": "question", "title": "T", "detail": "", "action": "", "target": "",
         "options": ["a", "b"], "multi": true, "status": "answered", "answer": "a",
         "decided_by": "user", "decided_at": 1.0, "created_at": 0, "always": false}
        """
        let data = json.data(using: .utf8)!
        let req = try JSONDecoder().decode(CompanionRequest.self, from: data)
        #expect(req.kind == .question)
        #expect(req.status == .answered)
        #expect(req.options == ["a", "b"])
        #expect(req.multi == true)
    }

    @Test("Encode EventIn")
    func testEncodeEventIn() throws {
        let event = EventIn(
            tool: "claude-code",
            session: "s1",
            name: "Claude Code",
            folder: "/tmp/proj",
            event: .step,
            text: "Read file",
            kind: "read"
        )
        let data = try JSONEncoder().encode(event)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        #expect(json["tool"] as? String == "claude-code")
        #expect(json["event"] as? String == "step")
        #expect(json["kind"] as? String == "read")
    }

    @Test("Encode RequestIn")
    func testEncodeRequestIn() throws {
        let request = RequestIn(
            tool: "codex",
            session: "s2",
            kind: .permission,
            title: "Run tests",
            action: "Bash",
            target: "pytest"
        )
        let data = try JSONEncoder().encode(request)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        #expect(json["tool"] as? String == "codex")
        #expect(json["kind"] as? String == "permission")
        #expect(json["action"] as? String == "Bash")
    }

    @Test("Encode DecideIn allow")
    func testEncodeDecideInAllow() throws {
        let decide = DecideIn(decision: .allow)
        let data = try JSONEncoder().encode(decide)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        #expect(json["decision"] as? String == "allow")
        #expect(json["answer"] == nil)
    }

    @Test("Encode DecideIn answer")
    func testEncodeDecideInAnswer() throws {
        let decide = DecideIn(decision: .answer, answer: "my answer")
        let data = try JSONEncoder().encode(decide)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        #expect(json["decision"] as? String == "answer")
        #expect(json["answer"] as? String == "my answer")
    }
}
import Foundation
import Testing
@testable import CompanionCore

@Suite("SSE Parser Tests")
struct SSEParserTests {

    @Test("Parse simple data event")
    func testParseSimpleDataEvent() {
        let parser = SSEParser()
        let chunk = "data: hello\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].data == "hello")
        #expect(events[0].event == nil)
        #expect(events[0].id == nil)
        #expect(events[0].isPing == false)
    }

    @Test("Parse event with type")
    func testParseEventWithType() {
        let parser = SSEParser()
        let chunk = "event: message\ndata: {\"foo\": \"bar\"}\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].event == "message")
        #expect(events[0].data == "{\"foo\": \"bar\"}")
    }

    @Test("Parse event with id")
    func testParseEventWithId() {
        let parser = SSEParser()
        let chunk = "id: 123\ndata: test\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].id == "123")
        #expect(events[0].data == "test")
    }

    @Test("Parse event with retry")
    func testParseEventWithRetry() {
        let parser = SSEParser()
        let chunk = "retry: 5000\ndata: test\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].retry == 5000)
    }

    @Test("Parse multi-line data")
    func testParseMultiLineData() {
        let parser = SSEParser()
        let chunk = "data: line 1\ndata: line 2\ndata: line 3\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].data == "line 1\nline 2\nline 3")
    }

    @Test("Parse ping comment")
    func testParsePingComment() {
        let parser = SSEParser()
        let chunk = ": ping\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].isPing == true)
        #expect(events[0].data == ": ping")
    }

    @Test("Parse colon comment")
    func testParseColonComment() {
        let parser = SSEParser()
        let chunk = ": this is a comment\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].isPing == true)
    }

    @Test("Parse split across chunks")
    func testParseSplitAcrossChunks() {
        let parser = SSEParser()

        let events1 = parser.parse("data: first")
        #expect(events1.count == 0)

        let events2 = parser.parse(" line\n\ndata: second\n\n")
        #expect(events2.count == 2)
        #expect(events2[0].data == "first line")
        #expect(events2[1].data == "second")
    }

    @Test("Parse partial then complete")
    func testParsePartialThenComplete() {
        let parser = SSEParser()

        _ = parser.parse("event: test\ndata: ")
        let events = parser.parse("partial\n\ndata: complete\n\n")

        #expect(events.count == 2)
        #expect(events[0].event == "test")
        #expect(events[0].data == "partial")
        #expect(events[1].data == "complete")
    }

    @Test("Flush remaining buffer")
    func testFlushRemainingBuffer() {
        let parser = SSEParser()
        _ = parser.parse("data: incomplete")
        let event = parser.flush()

        #expect(event != nil)
        #expect(event?.data == "incomplete")
    }

    @Test("Flush empty buffer")
    func testFlushEmptyBuffer() {
        let parser = SSEParser()
        let event = parser.flush()
        #expect(event == nil)
    }

    @Test("Parse companion state event")
    func testParseCompanionStateEvent() {
        let parser = SSEParser()
        let stateJson = "{\"version\": 5, \"mood\": \"working\", \"agents\": [], \"requests\": [], \"usage\": {\"rooks_month\": null, \"agents\": []}, \"rules\": []}"
        let chunk = "data: \(stateJson)\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].isPing == false)

        let data = events[0].data.data(using: .utf8)!
        let state = try! JSONDecoder().decode(CompanionState.self, from: data)
        #expect(state.version == 5)
        #expect(state.mood == .working)
    }

    @Test("Parse heartbeat ping")
    func testParseHeartbeatPing() {
        let parser = SSEParser()
        let chunk = ": ping\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].isPing == true)
    }

    @Test("Parse mixed events and pings")
    func testParseMixedEventsAndPings() {
        let parser = SSEParser()
        let stateJson = "{\"version\": 1, \"mood\": \"idle\", \"agents\": [], \"requests\": [], \"usage\": {\"rooks_month\": null, \"agents\": []}, \"rules\": []}"
        let chunk = "data: \(stateJson)\n\n\n: ping\n\n\ndata: \(stateJson)\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 3)
        #expect(events[0].isPing == false)
        #expect(events[1].isPing == true)
        #expect(events[2].isPing == false)
    }

    @Test("Empty data line")
    func testEmptyDataLine() {
        let parser = SSEParser()
        let chunk = "data:\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].data == "")
    }

    @Test("Data with leading space")
    func testDataWithLeadingSpace() {
        let parser = SSEParser()
        let chunk = "data:  hello\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].data == "hello")
    }

    @Test("Multiple events in one chunk")
    func testMultipleEventsInOneChunk() {
        let parser = SSEParser()
        let chunk = "data: first\n\n: ping\n\ndata: second\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 3)
        #expect(events[0].data == "first")
        #expect(events[1].isPing == true)
        #expect(events[2].data == "second")
    }

    @Test("Event with all fields")
    func testEventWithAllFields() {
        let parser = SSEParser()
        let chunk = "id: 42\nevent: update\nretry: 10000\ndata: payload\n\n"
        let events = parser.parse(chunk)

        #expect(events.count == 1)
        #expect(events[0].id == "42")
        #expect(events[0].event == "update")
        #expect(events[0].retry == 10000)
        #expect(events[0].data == "payload")
    }
}
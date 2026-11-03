import Foundation
import Testing
@testable import CompanionCore

@Suite("Config Discovery Tests", .serialized)
struct ConfigDiscoveryTests {

    private let tempDir: URL

    init() {
        self.tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("companion_test_\(UUID().uuidString)")
        try! FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
    }

    private func writeConfig(_ config: CompanionConfig, to url: URL) throws {
        let data = try JSONEncoder().encode(config)
        try data.write(to: url)
    }

    private func makeStore(for fileName: String = "companion.json") -> CompanionConfigStore {
        return CompanionConfigStore(fileURL: tempDir.appendingPathComponent(fileName))
    }

    @Test("Load config from default path")
    func testLoadConfigFromDefaultPath() async throws {
        let config = CompanionConfig(url: "http://127.0.0.1:9999", token: "test-token-123456789012345678901234")
        let fileURL = tempDir.appendingPathComponent("companion.json")
        try writeConfig(config, to: fileURL)

        let store = makeStore()
        let loaded = try await store.loadConfig()

        #expect(loaded.url == "http://127.0.0.1:9999")
        #expect(loaded.token == "test-token-123456789012345678901234")
    }

    @Test("Load config from custom path")
    func testLoadConfigFromCustomPath() async throws {
        let customPath = tempDir.appendingPathComponent("custom.json")
        let config = CompanionConfig(url: "http://127.0.0.1:8888", token: "custom-token-abcdefghijklmnopqrstuvwx")
        try writeConfig(config, to: customPath)

        let store = CompanionConfigStore(fileURL: customPath)
        let loaded = try await store.loadConfig()

        #expect(loaded.url == "http://127.0.0.1:8888")
        #expect(loaded.token == "custom-token-abcdefghijklmnopqrstuvwx")
    }

    @Test("Cache returns same config without force reload")
    func testCacheReturnsSameConfigWithoutForceReload() async throws {
        let config = CompanionConfig(url: "http://127.0.0.1:9999", token: "token1")
        try writeConfig(config, to: tempDir.appendingPathComponent("companion.json"))

        let store = makeStore()
        let first = try await store.loadConfig()
        let second = try await store.loadConfig(forceReload: false)

        #expect(first.url == second.url)
        #expect(first.token == second.token)
    }

    @Test("Force reload bypasses cache")
    func testForceReloadBypassesCache() async throws {
        let config1 = CompanionConfig(url: "http://127.0.0.1:9999", token: "token1")
        try writeConfig(config1, to: tempDir.appendingPathComponent("companion.json"))

        let store = makeStore()
        _ = try await store.loadConfig()

        // Without modifying the file, forceReload should still work (though file hasn't changed)
        let cached = try await store.loadConfig(forceReload: false)
        #expect(cached.token == "token1")

        let reloaded = try await store.loadConfig(forceReload: true)
        #expect(reloaded.token == "token1")

        // Now modify the file
        let config2 = CompanionConfig(url: "http://127.0.0.1:8888", token: "token2")
        try writeConfig(config2, to: tempDir.appendingPathComponent("companion.json"))

        // Small delay to ensure file modification time changes
        try await Task.sleep(nanoseconds: 1_000_000_000)

        // After file change, even without forceReload, it should reload
        let afterChange = try await store.loadConfig(forceReload: false)
        #expect(afterChange.token == "token2")

        let afterChangeForce = try await store.loadConfig(forceReload: true)
        #expect(afterChangeForce.token == "token2")
    }

    @Test("Invalidate cache clears cache")
    func testInvalidateCacheClearsCache() async throws {
        let config1 = CompanionConfig(url: "http://127.0.0.1:9999", token: "token1")
        try writeConfig(config1, to: tempDir.appendingPathComponent("companion.json"))

        let store = makeStore()
        _ = try await store.loadConfig()

        let config2 = CompanionConfig(url: "http://127.0.0.1:8888", token: "token2")
        try writeConfig(config2, to: tempDir.appendingPathComponent("companion.json"))

        await store.invalidateCacheForTesting()
        let reloaded = try await store.loadConfig(forceReload: false)
        #expect(reloaded.token == "token2")
    }

    @Test("Missing config file throws")
    func testMissingConfigFileThrows() async throws {
        let store = makeStore(for: "nonexistent.json")

        await #expect(throws: Error.self) {
            _ = try await store.loadConfig()
        }
    }

    @Test("Invalid JSON throws")
    func testInvalidJSONThrows() async throws {
        try "not json".write(to: tempDir.appendingPathComponent("companion.json"), atomically: true, encoding: .utf8)

        let store = makeStore()

        await #expect(throws: Error.self) {
            _ = try await store.loadConfig()
        }
    }

    @Test("Missing required fields throws")
    func testMissingRequiredFieldsThrows() async throws {
        let json = "{\"url\": \"http://127.0.0.1:9999\"}"
        try json.write(to: tempDir.appendingPathComponent("companion.json"), atomically: true, encoding: .utf8)

        let store = makeStore()

        await #expect(throws: Error.self) {
            _ = try await store.loadConfig()
        }
    }

    @Test("Config file permissions")
    func testConfigFilePermissions() async throws {
        let config = CompanionConfig(url: "http://127.0.0.1:9999", token: "test-token-123456789012345678901234")
        let fileURL = tempDir.appendingPathComponent("companion.json")
        try writeConfig(config, to: fileURL)

        let store = makeStore()
        _ = try await store.loadConfig()

        let attrs = try FileManager.default.attributesOfItem(atPath: fileURL.path)
        let perms = attrs[.posixPermissions] as? Int ?? 0
        #expect(perms & 0o400 != 0)
    }
}
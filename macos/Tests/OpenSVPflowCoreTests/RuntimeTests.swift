import Foundation
import Testing
@testable import OpenSVPflowCore

@Test func subprocessReturnsJSONWithoutLaunchingShell() async throws {
    let result = try await BoundedProcess.run(executable: URL(fileURLWithPath: "/usr/bin/printf"), arguments: ["%s", "{\"ok\":true,\"message\":\"A film's path\"}"], input: Data(), timeout: 2)
    let json = try JSONValue.parse(result.output)
    #expect(json["ok"] == .bool(true))
    #expect(json["message"] == .string("A film's path"))
    #expect(result.exitCode == 0)
}

@Test func subprocessTimeoutTerminatesItsChild() async throws {
    let clock = ContinuousClock()
    let start = clock.now
    await #expect(throws: ProcessFailure.self) {
        try await BoundedProcess.run(executable: URL(fileURLWithPath: "/bin/sleep"), arguments: ["10"], input: Data(), timeout: 0.1)
    }
    #expect(clock.now - start < .seconds(4))
}

@Test func cancellationStopsBoundedSubprocess() async throws {
    let task = Task {
        try await BoundedProcess.run(executable: URL(fileURLWithPath: "/bin/sleep"), arguments: ["10"], input: Data(), timeout: 20)
    }
    try await Task.sleep(for: .milliseconds(100))
    task.cancel()
    await #expect(throws: CancellationError.self) { try await task.value }
}

@Test func installerCopiesOnlyBundledTemplateIntoPrivateExecutableFile() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let resources = root.appendingPathComponent("Resources")
    let state = root.appendingPathComponent("State")
    try FileManager.default.createDirectory(at: resources.appendingPathComponent("player"), withIntermediateDirectories: true)
    let original = Data("#!/bin/bash\nprintf 'fixture only'\n".utf8)
    try original.write(to: resources.appendingPathComponent("player/install-homebrew.command"))
    let script = try InstallerTemplate.prepare(kind: "homebrew", resources: resources, stateDirectory: state)
    #expect(try Data(contentsOf: script) == original)
    #expect(script.path.hasPrefix(state.path + "/"))
    let attributes = try FileManager.default.attributesOfItem(atPath: script.path)
    #expect((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o700)
    #expect(throws: ProcessFailure.self) {
        try InstallerTemplate.prepare(kind: "../escape", resources: resources, stateDirectory: state)
    }
}

@Test func pythonDiscoveryUsesVspipeInterpreterWithoutExecutingIt() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let bin = root.appendingPathComponent("bin")
    try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
    let interpreter = bin.appendingPathComponent("python3.15")
    try Data().write(to: interpreter)
    try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: interpreter.path)
    try Data(("#!" + interpreter.path + "\n").utf8).write(to: bin.appendingPathComponent("vspipe"))
    try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: bin.appendingPathComponent("vspipe").path)
    #expect(RuntimeDiscovery.python(prefix: root, environment: [:]) == interpreter)
}

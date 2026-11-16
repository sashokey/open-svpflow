import Darwin
import Foundation

public struct PlaybackFailure: LocalizedError, Sendable {
    public let id: String
    public let title: String
    public let message: String
    public let rateError: Bool
    public var errorDescription: String? { message }
}

public enum ProcessFailure: LocalizedError, Sendable {
    case timeout
    case outputLimit
    case invalidInstaller
    case missingTemplate
    case backend(String)

    public var errorDescription: String? {
        switch self {
        case .timeout: "The operation took too long. You can retry."
        case .outputLimit: "The operation returned too much diagnostic output."
        case .invalidInstaller: "Unknown installation operation."
        case .missingTemplate: "The bundled installation instructions are missing. Download a complete application package."
        case .backend(let message): message
        }
    }
}

public struct ProcessOutput: Sendable {
    public let output: Data
    public let diagnostics: Data
    public let exitCode: Int32
}

private final class ProcessCancellation: @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false

    func cancel() { lock.withLock { cancelled = true } }
    var isCancelled: Bool { lock.withLock { cancelled } }
}

public enum BoundedProcess {
    public static func run(executable: URL, arguments: [String], input: Data, timeout: TimeInterval, environment: [String: String]? = nil) async throws -> ProcessOutput {
        let cancellation = ProcessCancellation()
        return try await withTaskCancellationHandler {
            try await Task.detached {
                let manager = FileManager.default
                let directory = manager.temporaryDirectory.appendingPathComponent("open-svpflow-command-" + UUID().uuidString)
                try manager.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
                defer { try? manager.removeItem(at: directory) }
                let inputURL = directory.appendingPathComponent("input")
                let outputURL = directory.appendingPathComponent("output")
                let errorURL = directory.appendingPathComponent("error")
                try input.write(to: inputURL)
                _ = manager.createFile(atPath: outputURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
                _ = manager.createFile(atPath: errorURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
                let inputHandle = try FileHandle(forReadingFrom: inputURL)
                let outputHandle = try FileHandle(forWritingTo: outputURL)
                let errorHandle = try FileHandle(forWritingTo: errorURL)
                defer {
                    try? inputHandle.close()
                    try? outputHandle.close()
                    try? errorHandle.close()
                }
                let process = Process()
                process.executableURL = executable
                process.arguments = arguments
                process.standardInput = inputHandle
                process.standardOutput = outputHandle
                process.standardError = errorHandle
                var processEnvironment = environment ?? ProcessInfo.processInfo.environment
                processEnvironment["HOMEBREW_NO_AUTO_UPDATE"] = "1"
                processEnvironment["HOMEBREW_NO_ANALYTICS"] = "1"
                processEnvironment["HOMEBREW_NO_INSTALL_CLEANUP"] = "1"
                processEnvironment["PYTHONDONTWRITEBYTECODE"] = "1"
                process.environment = processEnvironment
                if cancellation.isCancelled { throw CancellationError() }
                try process.run()
                let deadline = ProcessInfo.processInfo.systemUptime + timeout
                var failure: (any Error)?
                while process.isRunning {
                    if cancellation.isCancelled { failure = CancellationError(); break }
                    if ProcessInfo.processInfo.systemUptime >= deadline { failure = ProcessFailure.timeout; break }
                    let sizes = [outputURL, errorURL].compactMap { try? manager.attributesOfItem(atPath: $0.path)[.size] as? NSNumber }
                    if sizes.reduce(0, { $0 + $1.intValue }) > 2_097_152 { failure = ProcessFailure.outputLimit; break }
                    try await Task.sleep(for: .milliseconds(20))
                }
                if failure != nil && process.isRunning {
                    process.terminate()
                    let grace = ProcessInfo.processInfo.systemUptime + 1
                    while process.isRunning && ProcessInfo.processInfo.systemUptime < grace { try await Task.sleep(for: .milliseconds(20)) }
                    if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                }
                if process.isRunning { process.waitUntilExit() }
                if let failure { throw failure }
                let output = try Data(contentsOf: outputURL)
                let diagnostics = try Data(contentsOf: errorURL)
                guard output.count + diagnostics.count <= 2_097_152 else { throw ProcessFailure.outputLimit }
                return ProcessOutput(output: output, diagnostics: diagnostics, exitCode: process.terminationStatus)
            }.value
        } onCancel: {
            cancellation.cancel()
        }
    }
}

public enum RuntimeDiscovery {
    public static func brew(environment: [String: String] = ProcessInfo.processInfo.environment) -> URL? {
        executable("brew", prefix: URL(fileURLWithPath: "/opt/homebrew"), environment: environment)
    }

    public static func python(prefix: URL = URL(fileURLWithPath: "/opt/homebrew"), environment: [String: String] = ProcessInfo.processInfo.environment) -> URL? {
        if let pipe = executable("vspipe", prefix: prefix, environment: environment),
           let data = try? Data(contentsOf: pipe),
           let header = String(data: data.prefix(4096), encoding: .utf8)?.split(separator: "\n").first,
           header.hasPrefix("#!") {
            let interpreter = String(header.dropFirst(2)).trimmingCharacters(in: .whitespaces)
            let candidate = URL(fileURLWithPath: interpreter)
            if interpreter.hasPrefix("/"), !interpreter.contains(" "), candidate.lastPathComponent.hasPrefix("python"), FileManager.default.isExecutableFile(atPath: candidate.path) {
                return candidate
            }
        }
        return executable("python3", prefix: prefix, environment: environment)
    }

    private static func executable(_ name: String, prefix: URL, environment: [String: String]) -> URL? {
        let paths = [prefix.appendingPathComponent("bin").path] + (environment["PATH"] ?? "").split(separator: ":").map(String.init)
        return paths.lazy.filter { $0.hasPrefix("/") }.map { URL(fileURLWithPath: $0).appendingPathComponent(name) }
            .first { FileManager.default.isExecutableFile(atPath: $0.path) }
    }
}

public enum InstallerTemplate {
    public static func prepare(kind: String, resources: URL, stateDirectory: URL) throws -> URL {
        guard ["homebrew", "dependencies"].contains(kind) else { throw ProcessFailure.invalidInstaller }
        let source = resources.appendingPathComponent("player/install-" + kind + ".command")
        guard let data = try? Data(contentsOf: source) else { throw ProcessFailure.missingTemplate }
        let directory = stateDirectory.appendingPathComponent("Install-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let destination = directory.appendingPathComponent("Install.command")
        try data.write(to: destination, options: .withoutOverwriting)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: destination.path)
        return destination
    }
}

public struct BackendClient: Sendable {
    public let python: URL
    public let resources: URL
    public let stateDirectory: URL

    public init(python: URL, resources: URL, stateDirectory: URL) {
        self.python = python
        self.resources = resources
        self.stateDirectory = stateDirectory
    }

    public func call(_ command: String, request: JSONValue = .object([:]), timeout: TimeInterval = 15) async throws -> JSONValue {
        let script = resources.appendingPathComponent("player/application.py")
        let arguments = ["-B", script.path, command, "--state-dir", stateDirectory.path, "--bundle-root", resources.path]
        let result = try await BoundedProcess.run(executable: python, arguments: arguments, input: try request.encoded(), timeout: timeout)
        let value: JSONValue
        do { value = try JSONValue.parse(result.output) }
        catch {
            let diagnostics = String(data: result.diagnostics, encoding: .utf8) ?? ""
            throw ProcessFailure.backend(diagnostics.isEmpty ? "The playback helper returned an unreadable response." : String(diagnostics.suffix(4096)))
        }
        guard result.exitCode == 0, value["ok"]?.boolean == true else {
            let message = value["message"]?.string ?? "The playback helper could not complete the operation."
            if let id = value["failure_id"]?.string {
                throw PlaybackFailure(id: id, title: value["file_title"]?.string ?? "", message: message,
                                      rateError: value["rate_error"]?.boolean == true)
            }
            throw ProcessFailure.backend(message)
        }
        return value
    }
}

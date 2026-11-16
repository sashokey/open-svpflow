import Foundation

public enum JSONValueError: Error, Equatable {
    case invalidPath(String)
    case invalidDescriptor
}

public enum JSONValue: Codable, Hashable, Sendable {
    case object([String: JSONValue])
    case array([JSONValue])
    case string(String)
    case number(Double)
    case bool(Bool)
    case null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([String: JSONValue].self) { self = .object(value) }
        else { self = .array(try container.decode([JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    public static func parse(_ data: Data) throws -> JSONValue {
        try JSONDecoder().decode(JSONValue.self, from: data)
    }

    public func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(self)
    }

    public subscript(_ key: String) -> JSONValue? {
        guard case .object(let object) = self else { return nil }
        return object[key]
    }

    public var string: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    public var number: Double? {
        if case .number(let value) = self { return value }
        return nil
    }

    public var boolean: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    public var array: [JSONValue] {
        if case .array(let value) = self { return value }
        return []
    }

    public var text: String {
        switch self {
        case .string(let value): return value
        case .number(let value): return value.rounded() == value ? String(format: "%.0f", value) : String(value)
        case .bool(let value): return value ? "true" : "false"
        default: return ""
        }
    }

    public func value(at path: String) -> JSONValue? {
        path.split(separator: ".").reduce(Optional(self)) { value, key in value?[String(key)] }
    }

    public mutating func set(_ value: JSONValue, at path: String) throws {
        let keys = path.split(separator: ".").map(String.init)
        guard !keys.isEmpty else { throw JSONValueError.invalidPath(path) }
        self = try replacing(value, keys: ArraySlice(keys), path: path)
    }

    private func replacing(_ value: JSONValue, keys: ArraySlice<String>, path: String) throws -> JSONValue {
        guard let key = keys.first, case .object(var object) = self else { throw JSONValueError.invalidPath(path) }
        if keys.count == 1 { object[key] = value }
        else { object[key] = try (object[key] ?? .object([:])).replacing(value, keys: keys.dropFirst(), path: path) }
        return .object(object)
    }
}

public struct SettingOption: Identifiable, Hashable, Sendable {
    public let value: JSONValue
    public let label: String
    public var id: JSONValue { value }
}

public struct SettingDescriptor: Identifiable, Sendable {
    public let path: String
    public let label: String
    public let group: String
    public let kind: String
    public let options: [SettingOption]
    public let minimum: Double?
    public let maximum: Double?
    public let step: Double?
    public let help: String
    public let supported: Bool
    public var id: String { path }

    public init(_ value: JSONValue) throws {
        guard let path = value["path"]?.string, let label = value["label"]?.string, let kind = value["kind"]?.string else {
            throw JSONValueError.invalidDescriptor
        }
        self.path = path
        self.label = label
        self.kind = kind
        group = value["group"]?.string ?? "Advanced"
        options = value["options"]?.array.compactMap { option in
            guard let value = option["value"], let label = option["label"]?.string else { return nil }
            return SettingOption(value: value, label: label)
        } ?? []
        minimum = value["min"]?.number
        maximum = value["max"]?.number
        step = value["step"]?.number
        help = value["help"]?.string ?? ""
        supported = value["supported"]?.boolean ?? true
    }
}
